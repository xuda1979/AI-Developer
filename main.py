#!/usr/bin/env python3
import os
import argparse
import openai
import subprocess
import json
import pathlib
import sys
import tempfile
import re


def gather_files(root: str = '.') -> dict:
    """
    Recursively gather all files in the project, including code and text files (such as .py, .txt, .md, .json, .yaml, .toml, .tex)
    and command output logs from previous iterations. Skip any files inside the .git directory.
    Returns a dictionary mapping relative file paths to their contents.
    """
    files = {}
    for path in pathlib.Path(root).rglob('*'):
        if path.is_file():
            # skip version control metadata directories
            if '.git' in path.parts:
                continue
            try:
                # read text files using UTF-8 ignoring errors; binary files may raise exception
                content = path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                # skip files that cannot be read as text
                continue
            # store with POSIX style relative path
            files[str(path.relative_to(root))] = content
    return files


def apply_patch(diff_text: str) -> bool:
    """
    Apply a unified diff to the local repository using the 'patch' command.
    Returns True if the patch applied successfully, False otherwise.
    """
    if not diff_text.strip():
        return True
    try:
        subprocess.run(['patch', '-p1', '-u'], input=diff_text.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print("Error applying patch:", e.stderr.decode('utf-8', errors='ignore'), file=sys.stderr)
        return False


def run_commands(commands: list, iteration: int) -> str:
    """
    Execute shell commands sequentially and capture their output.
    The output from each command is stored in a JSON file for the given iteration.
    Returns the path to the JSON log file.
    """
    output_log = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            output_log.append({
                'command': cmd,
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
            })
        except Exception as e:
            output_log.append({
                'command': cmd,
                'error': str(e),
            })
    log_path = f'command_output_iteration_{iteration}.json'
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(output_log, f, indent=2)
    return log_path


def parse_model_response(response: str) -> tuple:
    """
    Parse the model's response to extract the commit message, unified diff, and list of commands to run.
    The response is expected to contain sections labelled COMMIT_MESSAGE:, DIFF:, and COMMANDS:.
    """
    commit_msg = ''
    diff_text = ''
    commands = []
    commit_match = re.search(r'COMMIT_MESSAGE:\s*([\s\S]*?)\nDIFF:', response)
    diff_match = re.search(r'DIFF:\s*([\s\S]*?)\nCOMMANDS:', response)
    commands_match = re.search(r'COMMANDS:\s*([\s\S]*)', response)
    if commit_match:
        commit_msg = commit_match.group(1).strip()
    if diff_match:
        diff_text = diff_match.group(1).strip()
    if commands_match:
        commands = [line.strip() for line in commands_match.group(1).splitlines() if line.strip()]
    return commit_msg, diff_text, commands


def main() -> None:
    parser = argparse.ArgumentParser(description='Iteratively improve a project with OpenAI.')
    parser.add_argument('--max-iterations', type=int, default=1, help='Maximum number of review iterations')
    parser.add_argument('--model', type=str, default='gpt-4-1106-preview', help='OpenAI model name')
    args = parser.parse_args()

    openai.api_key = os.getenv('OPENAI_API_KEY')
    if not openai.api_key:
        print("Error: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    for iteration in range(1, args.max_iterations + 1):
        print(f'Iteration {iteration}/{args.max_iterations}')

        # gather project files, including logs from previous iterations
        files = gather_files('.')
        # build prompt
        system_prompt = (
            "You are an AI developer agent tasked with improving a software project.\n"
            "When provided with the current project files and command outputs from previous iterations, analyze the entire project and propose improvements.\n"
            "Respond strictly using the following format:\n"
            "COMMIT_MESSAGE:\n<commit message text>\n"
            "DIFF:\n<unified git diff patch>\n"
            "COMMANDS:\n<command1>\n<command2>\n"
            "If no changes are needed, leave the diff and commands sections empty.\n"
        )
        user_prompt = (
            "Here is the current project as a JSON object mapping file paths to their contents.\n"
            "This includes all code files, text files (.py, .txt, .md, .json, .yaml, .toml, .tex), and command output logs from previous iterations.\n"
            + json.dumps(files, indent=2) +
            "\nReview this project and the command outputs. Then suggest and apply improvements.\n"
            "Return your answer in the specified format."
        )
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

        response = openai.ChatCompletion.create(
            model=args.model,
            messages=messages,
            temperature=0.0,
        )
        assistant_reply = response['choices'][0]['message']['content']
        commit_message, diff_text, commands = parse_model_response(assistant_reply)

        # apply diff
        if diff_text:
            success = apply_patch(diff_text)
            if not success:
                print("Failed to apply patch. Exiting.")
                sys.exit(1)

        # run commands and log outputs
        log_path = None
        if commands:
            log_path = run_commands(commands, iteration)
            # include the new log file for next iteration
            files[log_path] = pathlib.Path(log_path).read_text(encoding='utf-8')

        # stage all changes and commit if there are modifications or commands
        if diff_text or commands:
            subprocess.run(['git', 'add', '.'])
            subprocess.run(['git', 'commit', '-m', commit_message])
        else:
            print("No changes proposed by model.")
            break

if __name__ == '__main__':
    main()
