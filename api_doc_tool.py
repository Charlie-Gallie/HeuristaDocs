import argparse
import os
import subprocess
from clang.cindex import Index, CursorKind, Config

# Configure libclang path
if os.name == 'nt':
    default_libclang_path = "S:/Program Files/LLVM/bin/libclang.dll"
    if os.path.exists(default_libclang_path):
        Config.set_library_file(default_libclang_path)

symbol_cache = {}

std_types = {
    "size_t", "intptr_t", "uintptr_t", "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t"
}

def extract_symbols(files):
    index = Index.create()
    all_symbols = []
    for filepath in files:
        tu = index.parse(filepath)
        for cursor in tu.cursor.get_children():
            if cursor.location.file and not str(cursor.location.file).startswith("/usr"):
                if cursor.kind in [CursorKind.FUNCTION_DECL, CursorKind.STRUCT_DECL, CursorKind.TYPEDEF_DECL, CursorKind.ENUM_DECL, CursorKind.MACRO_DEFINITION]:
                    if cursor.spelling and cursor.spelling not in std_types:
                        symbol_cache[cursor.spelling] = {
                            "kind": cursor.kind.name.lower(),
                            "file": filepath,
                            "line": cursor.location.line
                        }
                        all_symbols.append(cursor.spelling)
    return all_symbols

def get_declaration(symbol):
    import re
    info = symbol_cache[symbol]
    with open(info["file"], encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    snippet_lines = lines[info["line"] - 1 : info["line"] + 10]

    # Include type definitions and macro constants used in the snippet
    full_context = set()
    for line in snippet_lines:
        tokens = re.findall(r"\b\w+\b", line)
        for token in tokens:
            if token in symbol_cache and symbol_cache[token]['kind'] in {"typedef_decl", "enum_decl", "macro_definition"}:
                context_info = symbol_cache[token]
                with open(context_info["file"], encoding="utf-8", errors="replace") as f2:
                    ctx_lines = f2.readlines()
                    def_line = context_info["line"] - 1
                    full_context.add(''.join(ctx_lines[def_line:def_line+5]).strip())

    snippet = ''.join(snippet_lines).strip()
    return '\n'.join(full_context) + '\n' + snippet

import openai

def run_ollama(symbol, snippet):
    kind = symbol_cache[symbol]['kind']
    style = {
        'function_decl': """Document this C function in detail. Include the name, full parameter list with their types and possible bounds, return type, and a detailed description of what the function does. If observable, also include:

- Complexity (e.g. time or space complexity)
- Side effects (e.g. mutating global state, I/O)
- Preconditions or error conditions (e.g. return values that indicate errors)

Describe parameters and return value using <dl>, with <dt> and <dd> for names and descriptions.""",

        'struct_decl': 'Document this struct including the struct name, each field name, its type, and purpose. Include any observable struct-level invariants or alignment notes.',

        'typedef_decl': 'Document this typedef. Explain what it abstracts, the underlying type, and how it is typically used.',

        'enum_decl': 'Document this enum type in full detail. List all enumerators with their values and what they represent. Mention where each enumerator might be used if observable. Use a <table> or <dl> for structure.',

        'macro_definition': 'Document this macro constant. Describe its replacement value and how it is expected to be used. Mention any semantic effect it has on compilation.'
    }.get(kind, 'Document this C symbol. Structure the description according to its type.')

    prompt = f"""
You are an expert technical documentation engine.

You are provided with a C {kind} definition and its relevant context below. {style}

<context>
{snippet.strip()}
</context>

Output ONLY clean, semantic HTML inside a <section>. Do NOT include markdown, guesses, summaries, or template filler.

✅ Use <h2>, <dl>, <dt>, <dd>, <code>, <p>, etc. for structure.
✅ Do NOT hallucinate any meaning. Describe only what is explicit in the code.
✅ Start output with <section> and end with </section>.

Do not add anything other than what I've asked for. Do NOT use markdown formatting. Provide raw HTML only.
"""

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a precise C documentation generator that only outputs HTML."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )
    return response.choices[0].message.content.strip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--generate", help="Text file containing symbols to generate")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()

    symbols = extract_symbols(args.files)

    if args.discover:
        for sym in symbols:
            print(sym)
        return

    if args.generate:
        with open(args.generate) as f:
            targets = [line.strip() for line in f if line.strip() in symbols]
        for sym in targets:
            decl = get_declaration(sym)
            print(run_ollama(sym, decl))

if __name__ == "__main__":
    main()
