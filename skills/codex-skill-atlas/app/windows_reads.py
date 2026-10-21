"""Conservative recognition of literal PowerShell file reads; never executes scripts."""
import os
from pathlib import Path, PureWindowsPath
import re

SHELLS = {'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe'}
READERS = {'get-content', 'gc', 'cat', 'type'}


def tokens(command):
    # PowerShell uses backticks, not backslashes, for escaping. Keep quoted
    # script text intact even when a quote starts after an assignment operator.
    result, word, quote = [], [], None
    for char in command:
        if quote:
            word.append(char)
            if char == quote:
                quote = None
        elif char in '\"\'':
            quote = char
            word.append(char)
        elif char in '{}':
            # Script blocks and variable expressions need a full PS parser.
            # Allow braced environment variables as part of a literal path.
            word.append(char)
        elif char.isspace() or char in ';&|()':
            if word:
                result.append(''.join(word));word = []
            if char in ';&|()' or char == '\n':
                result.append(';' if char == '\n' else char)
        else:
            word.append(char)
    if quote:
        raise ValueError('Unclosed PowerShell quote')
    if word:
        result.append(''.join(word))
    return result


def unquote(value):
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'' else value


def powershell_reads(command):
    """Return literal paths, or None when the command belongs to the POSIX parser.

    Unsupported scripts, here-strings and encoded commands are not guessed. Structured
    Codex read actions remain available to the caller for those cases.
    """
    try:
        words = tokens(command)
    except ValueError:
        return [] if os.name == 'nt' else None
    if not words:
        return []
    first = PureWindowsPath(unquote(words[0])).name.lower()
    if first in SHELLS:
        lowered = [w.lower() for w in words]
        marker = next((i for i,w in enumerate(lowered) if w in {'-command', '-c'}), None)
        if marker is None or marker+1 >= len(words):
            return []
        rest = words[marker+1:]
        command = unquote(rest[0]) if len(rest) == 1 else ' '.join(rest)
        try:
            words = tokens(command)
        except ValueError:
            return []
    elif os.name != 'nt' and not any(w.lower() in {'get-content', 'gc'} for w in words):
        return None
    # Quoted script bodies are data; a deliberately small parser must not interpret them.
    has_script_block = any(
        any(c in re.sub(r'\$\{(?:env:)?(?:HOME|USERPROFILE|CODEX_HOME)\}', '', w, flags=re.I) for c in '{}')
        for w in words if not w.startswith(('"', "'")))
    if re.search(r"(?m)@['\"]\s*$", command) or '`' in command or has_script_block:
        return []
    segments, current = [], []
    for word in words+[';']:
        if word and set(word) <= set(';&|()'):
            segments.append(current);current=[]
        else:
            current.append(word)
    paths=[]
    for segment in segments:
        if not segment or segment[0].lower() not in READERS:
            continue
        args=segment[1:]
        candidates=[]
        i=0
        while i<len(args):
            arg=args[i]
            lower=arg.lower()
            if lower in {'-raw', '-force'}:
                i+=1;continue
            if lower in {'-totalcount', '-head', '-tail', '-readcount', '-encoding', '-erroraction'}:
                i+=2;continue
            if lower in {'-path', '-literalpath'}:
                i+=1
                if i>=len(args):
                    candidates=[];break
                arg=args[i]
            elif arg.startswith('-'):
                candidates=[];break
            path=unquote(arg)
            if not arg.startswith("'"):
                def expand(match):
                    name=(match[1] or match[2]).upper()
                    if name=='HOME':return str(Path.home())
                    return os.environ.get(name, match[0])
                path=re.sub(r'\$(?:\{(?:env:)?(HOME|USERPROFILE|CODEX_HOME)\}|(?:env:)?(HOME|USERPROFILE|CODEX_HOME)\b)', expand, path, flags=re.I)
            if path.lower().endswith('skill.md') and not any(c in path for c in '$*?'):
                candidates.append(path)
            i+=1
        paths.extend(candidates)
    return paths
