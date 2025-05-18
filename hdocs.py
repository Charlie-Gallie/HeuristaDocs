import sys
import os
import json
import openai
from clang import cindex
from clang.cindex import CursorKind

# Point to LLVM's libclang
cindex.Config.set_library_path(r"S:\Program Files\LLVM\bin")

# Configure OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")


def extract_symbols_from_file(source_path, clang_args=None, base_dir=None):
    """
    Parse a C/C++ source file, collect all user-defined symbols and types,
    and record references between them.
    """
    index = cindex.Index.create()
    if clang_args is None:
        clang_args = []
    base_dir = base_dir or os.path.dirname(os.path.abspath(source_path))

    try:
        tu = index.parse(source_path, args=clang_args)
    except Exception as e:
        print(f"Error parsing {source_path}: {e}")
        return []

    def is_user_file(node):
        loc = node.location.file
        if not loc:
            return False
        path = os.path.abspath(loc.name)
        return path.startswith(os.path.abspath(base_dir))

    # First pass: collect type definitions
    definitions = set()
    def collect_defs(node):
        if node.kind != CursorKind.TRANSLATION_UNIT and not is_user_file(node):
            return
        if node.kind in (
            CursorKind.ENUM_DECL,
            CursorKind.STRUCT_DECL,
            CursorKind.CLASS_DECL,
            CursorKind.UNION_DECL,
            CursorKind.TYPEDEF_DECL
        ) and node.spelling:
            definitions.add(node.spelling)
        for child in node.get_children():
            collect_defs(child)
    collect_defs(tu.cursor)

    symbols = []
    def extract_body_and_refs(func_node):
        type_refs = set()
        body_tokens = []
        def gather_refs(node):
            if node.kind == CursorKind.TYPE_REF and node.spelling in definitions:
                type_refs.add(node.spelling)
            for c in node.get_children():
                gather_refs(c)
        for child in func_node.get_children():
            if child.kind == CursorKind.COMPOUND_STMT:
                for tok in child.get_tokens():
                    body_tokens.append(tok.spelling)
                gather_refs(child)
        return type_refs, ' '.join(body_tokens)

    def visit(node):
        if node.kind != CursorKind.TRANSLATION_UNIT and not is_user_file(node):
            return
        kind = node.kind
        if kind in (CursorKind.FUNCTION_DECL, CursorKind.CXX_METHOD) and is_user_file(node):
            if not node.is_definition():
                return
            parent = node.semantic_parent
            scope = f"{parent.spelling}::" if parent and parent.spelling else ""
            name = scope + node.spelling
            ret_type = node.result_type.spelling
            parameters = [(arg.spelling, arg.type.spelling) for arg in node.get_arguments()]
            body_refs, body = extract_body_and_refs(node)
            symbols.append({
                'type': 'function',
                'name': name,
                'return_type': ret_type,
                'parameters': parameters,
                'type_references': list(body_refs),
                'body': body
            })
        elif kind == CursorKind.ENUM_DECL and is_user_file(node):
            enumerators = [c.spelling for c in node.get_children() if c.kind == CursorKind.ENUM_CONSTANT_DECL]
            symbols.append({'type': 'enum', 'name': node.spelling, 'enumerators': enumerators})
        elif kind == CursorKind.STRUCT_DECL and is_user_file(node):
            fields = [(c.spelling, c.type.spelling) for c in node.get_children() if c.kind == CursorKind.FIELD_DECL]
            methods = [c.spelling for c in node.get_children() if c.kind == CursorKind.CXX_METHOD and c.is_definition()]
            symbols.append({'type': 'struct', 'name': node.spelling, 'fields': fields, 'methods': methods})
        elif kind == CursorKind.CLASS_DECL and is_user_file(node):
            fields = [(c.spelling, c.type.spelling) for c in node.get_children() if c.kind == CursorKind.FIELD_DECL]
            methods = []
            for c in node.get_children():
                if c.kind == CursorKind.CXX_METHOD and c.is_definition():
                    qualifier = f"{c.semantic_parent.spelling}::" if c.semantic_parent and c.semantic_parent.spelling else ""
                    methods.append(qualifier + c.spelling)
            symbols.append({'type': 'class', 'name': node.spelling, 'fields': fields, 'methods': methods})
        elif kind == CursorKind.UNION_DECL and is_user_file(node):
            fields = [(c.spelling, c.type.spelling) for c in node.get_children() if c.kind == CursorKind.FIELD_DECL]
            symbols.append({'type': 'union', 'name': node.spelling, 'fields': fields})
        elif kind == CursorKind.TYPEDEF_DECL and is_user_file(node):
            symbols.append({'type': 'typedef', 'name': node.spelling, 'underlying': node.underlying_typedef_type.spelling})
        for child in node.get_children():
            visit(child)
    visit(tu.cursor)

    # Enrich references
    symbol_map_local = {s['name']: s for s in symbols}
    for s in symbols:
        if s['type'] == 'function':
            base_ret = s['return_type'].replace('*', '').replace('&', '').replace('const', '').strip()
            if base_ret in definitions:
                s['type_references'].append(base_ret)
            for _, ptype in s['parameters']:
                base_p = ptype.replace('*', '').replace('&', '').replace('const', '').strip()
                if base_p in definitions:
                    s['type_references'].append(base_p)
            if '::' in s['name']:
                cls = s['name'].split('::')[0]
                parent = symbol_map_local.get(cls)
                if parent:
                    for _, ftype in parent.get('fields', []):
                        base_f = ftype.replace('*', '').replace('&', '').replace('const', '').strip()
                        if base_f in definitions:
                            s['type_references'].append(base_f)
        if s['type'] == 'typedef':
            base_t = s['underlying'].replace('*', '').replace('&', '').replace('const', '').strip()
            if base_t in definitions:
                s.setdefault('type_references', []).append(base_t)
        if s['type'] in ['struct','class','union']:
            for _, ftype in s['fields']:
                base_f = ftype.replace('*', '').replace('&', '').replace('const', '').strip()
                if base_f in definitions:
                    s.setdefault('type_references', []).append(base_f)
        if 'type_references' in s:
            s['type_references'] = list(dict.fromkeys(s['type_references']))

    return symbols


def build_prompt_for_symbol(symbol, symbol_map):
    defs = [symbol]
    for ref in symbol.get('type_references', []):
        if ref in symbol_map:
            defs.append(symbol_map[ref])
    for field in symbol.get('fields', []):
        typ = field[1]
        if typ in symbol_map:
            defs.append(symbol_map[typ])
    if symbol['type'] == 'typedef':
        typ = symbol['underlying']
        if typ in symbol_map:
            defs.append(symbol_map[typ])
    unique_defs = list({d['name']: d for d in defs}.values())
    defs_json = json.dumps(unique_defs, indent=2)

    anchor_instructions = (
        "When mentioning any referenced symbol, wrap its name in an anchor tag linking to its definition, "
        "e.g., <a href=\"#{NAME}\">{NAME}</a>. Ensure each symbol section has an id matching its name."
    )

    prompt_header = f"""
You are an expert C and C++ documentation generator using HTML. Produce detailed and informative HTML documentation to the best of your abilities. If you are inferring something, ensure you show that and do not portray it as fact.
I will provide a JSON structure which provides an overview of the symbol you must write documentation for.

Context definitions (JSON):
{defs_json}

Symbol: {symbol['name']} ({symbol['type']})

{anchor_instructions}

Use the template below:
"""

    # Select template based on type
    if symbol['type'] == 'function':
        template = '''
<article class="symbol-doc" id="{NAME}">
  <header>
    <h1><a href="#''' + "{NAME}" + '''">{NAME}</a></h1>
    <h2>Function</h2>
  </header>
  <section id="{NAME}-signature" class="signature">
    <h3>Signature</h3>
    <pre>{SIGNATURE}</pre>
  </section>
  <section id="{NAME}-description" class="description">
    <h3>Description</h3>
    <p>{DESCRIPTION}</p>
  </section>
  <section id="{NAME}-return" class="return">
    <h3>Return Value</h3>
    <p>{RETURN_EXPLANATION}</p>
  </section>
  <section id="{NAME}-params" class="parameters">
    <h3>Parameters</h3>
    <ul>
      {{#PARAMS}}<li><strong><a href="#{{NAME}}">{{NAME}}</a></strong> ({{TYPE}}): {{DESCRIPTION}} [Bounds: {{BOUNDS}}]</li>{{/PARAMS}}
    </ul>
  </section>
  <section id="{NAME}-complexity" class="complexity">
    <h3>Complexity</h3>
    <p>Time Complexity: {TIME_COMPLEXITY}; Space Complexity: {SPACE_COMPLEXITY}</p>
  </section>
  <section id="{NAME}-side-effects" class="side-effects">
    <h3>Side Effects</h3>
    <p>{SIDE_EFFECTS}</p>
  </section>
</article>
'''
    elif symbol['type'] == 'enum':
        template = '''
<article class="symbol-doc" id="{NAME}">
  <header>
    <h1><a href="#''' + "{NAME}" + '''">{NAME}</a></h1>
    <h2>Enumeration</h2>
  </header>
  <section id="{NAME}-enumerators" class="enumerators">
    <h3>Members</h3>
    <ul>
      {{#ENUMS}}<li><a href="#''' + "{NAME}" + '''">{{NAME}}</a></li>{{/ENUMS}}
    </ul>
  </section>
  <section id="{NAME}-size" class="enum-size">
    <h3>Size</h3>
    <p>{ENUM_SIZE}</p>
  </section>
</article>
'''
    elif symbol['type'] in ('struct', 'class', 'union'):
        kind = symbol['type'].capitalize()
        template = f'''
<article class="symbol-doc" id="{symbol['name']}">
  <header>
    <h1><a href="#{symbol['name']}">{symbol['name']}</a></h1>
    <h2>{kind}</h2>
  </header>
  <section id="{symbol['name']}-fields" class="members">
    <h3>Fields</h3>
    <ul>
      {{#FIELDS}}<li><a href="#{{NAME}}"><strong>{{NAME}}</strong></a>: {{TYPE}}</li>{{/FIELDS}}
    </ul>
  </section>
  <section id="{symbol['name']}-methods" class="methods">
    <h3>Methods</h3>
    <ul>
      {{#METHODS}}<li><a href="#{{NAME}}">{{NAME}}()</a></li>{{/METHODS}}
    </ul>
  </section>
</article>
'''
    elif symbol['type'] == 'typedef':
        template = '''
<article class="symbol-doc" id="{NAME}">
  <header>
    <h1><a href="#''' + "{NAME}" + '''">{NAME}</a></h1>
    <h2>Type Alias</h2>
  </header>
  <section id="{NAME}-underlying" class="alias">
    <h3>Underlying Type</h3>
    <p><a href="#''' + "{UNDERLYING}" + '''">{UNDERLYING}</a></p>
  </section>
</article>
'''
    else:
        template = ''
        
    footer = """
    
Provide only the populated HTML code without any extra acknowledgements, markdown formatting, notes, etc. I need raw HTML code.
"""

    full_prompt = prompt_header + template + footer
    return full_prompt

def send_documentation_prompt(prompt):
    """
    Send the given prompt to OpenAI and return the response.
    """
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that generates documentation."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )
    return response.choices[0].message.content.strip()


def scan_directory(path, clang_args=None, base_dir=None):
    """
    Recursively scan a directory for C/C++ source files and extract symbols.
    """
    all_symbols = []
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith(('.c', '.cpp', '.cc', '.cxx', '.h', '.hpp')):
                full_path = os.path.join(root, file)
                syms = extract_symbols_from_file(full_path, clang_args, base_dir=base_dir or path)
                all_symbols.extend(syms)
    return all_symbols


def main():
    if len(sys.argv) < 2:
        print("Usage: python symbol_extractor.py <file_or_directory> [clang_arg1 clang_arg2 ...]")
        sys.exit(1)

    target = sys.argv[1]
    clang_args = sys.argv[2:]

    if os.path.isdir(target):
        symbols = scan_directory(target, clang_args)
    else:
        base_dir = os.path.dirname(os.path.abspath(target))
        symbols = extract_symbols_from_file(target, clang_args, base_dir)

    # Deduplicate symbols by type and name
    unique = {}
    for s in symbols:
        key = (s['type'], s['name'])
        if key not in unique:
            unique[key] = s
    symbols = list(unique.values())

    # Build symbol map
    symbol_map = {sym['name']: sym for sym in symbols}

    # Prepare output file and write header + CSS
    output_file = 'documentation.html'
    css = '''
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
.symbol-doc { border: 1px solid #ccc; padding: 15px; margin-bottom: 20px; border-radius: 5px; background: #f9f9f9; }
.symbol-doc header h1 { margin: 0; font-size: 24px; }
.symbol-doc header h2 { margin: 5px 0 15px; font-size: 18px; color: #555; }
.section { margin-bottom: 10px; }
.signature { background: #eee; padding: 10px; }
</style>
'''
    # Open once for writing; we'll append snippets as we go
    with open(output_file, 'w', encoding='utf-8') as f:
        # Write the document start
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Documentation</title>
""")
        f.write(css)
        f.write("""
</head>
<body>
""")
        f.flush()

        for sym in symbols:
            prompt = build_prompt_for_symbol(sym, symbol_map)
            print(f"--- Prompt for {sym['name']} ---")
            print(prompt)

            html = send_documentation_prompt(prompt)
            print(html)
            print()

            # Append the returned HTML block and flush immediately
            f.write(html + "\n")
            f.flush()

        # After all symbols, write closing tags
        f.write("""</body>
</html>""")
        f.flush()

    print(f"Documentation progressively written to {output_file}")


if __name__ == '__main__':
    main()
