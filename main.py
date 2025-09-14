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


def gather_files(root='.'):
    files = {}
    for path in pathlib.Path(root).rglob('*'):
        if path.is_file():
            # skip .git directory and hidden files
            if '.git' in path.parts:
                continue
            if path.name.startswith('.'):
                continue
            try:
                content = path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            files[str(path)] = content
    return files


def apply_patch(diff_text):
    with tempfile.NamedTemporaryFile('w', delete=False) as f:
        f.write(diff_text)
        patch_file = f.name
    try:
        result = subprocess.run(['patch', '-p1', '-i', patch_file], capture_output=True, text=True)
        if result.returncode != 0:
            print('Patch failed:', result.stdout, result.stderr)
            return False
        return True
    finally:
        os.remove(patch_file)


def run_commands(commands, iteration):
    outputs = {}
    for cmd in commands:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        outputs[cmd] = {
            'returncode': proc.returncode,
            'stdout': proc.stdout,
            'stderr': proc.stderr,
        }
    out_filename = f'command_output_iteration_{iteration}.json'
    with open(out_filename, 'w') as f:
        json.dump(outputs, f, indent=2)
    return out_filename


def parse_model_response(response):
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


def main():
    parser = argparse.ArgumentParser(description='AI Developer iterative assistant')
    parser.add_argument('--max-iteration', type=int, default=1, help='maximum iterations')
    parser.add_argument('--model', type=str, default='gpt-4', help='OpenAI model to use')
    args = parser.parse_args()

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print('Please set OPENAI_API_KEY environment variable.')
        sys.exit(1)
    openai.api_key = api_key

    for i in range(1, args.max_iteration + 1):
        files = gather_files('.')
        system_prompt = (
            'You are an AI coding assistant. Review the provided project files and suggest improvements. '
            'Respond using the following format:\n'
            'COMMIT_MESSAGE:\n<commit message text>\n'
            'DIFF:\n<unified git diff patch>\n'
            'COMMANDS:\n<command1>\n<command2>\n...'
        )
        user_prompt = 'Here is the current project files as a JSON object mapping file paths to their contents:\n' + json.dumps(files, indent=2) + '\n' + \
                      'Review this whole project. Then modify the project and output the commit message, diff, and commands as specified.'
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]
        completion = openai.ChatCompletion.create(model=args.model, messages=messages, temperature=0)
        response_text = completion.choices[0].message['content']
        commit_msg, diff_text, commands = parse_model_response(response_text)
        if not diff_text:
            print('No diff returned by model. Stopping.')
            break
        if not apply_patch(diff_text):
            print('Failed to apply patch. Stopping.')
            break
        if commands:
            run_commands(commands, i)
        subprocess.run(['git', 'add', '-A'])
        subprocess.run(['git', 'commit', '-m', commit_msg])


if __name__ == '__main__':
    main()
