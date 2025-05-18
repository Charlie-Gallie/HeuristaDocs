import os
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext

STD_TYPES = {
    "size_t", "ptrdiff_t", "intptr_t", "uintptr_t", "va_list",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int_least8_t", "int_least16_t", "int_least32_t", "int_least64_t",
    "uint_least8_t", "uint_least16_t", "uint_least32_t", "uint_least64_t",
    "int_fast8_t", "int_fast16_t", "int_fast32_t", "int_fast64_t",
    "uint_fast8_t", "uint_fast16_t", "uint_fast32_t", "uint_fast64_t",
    "intmax_t", "uintmax_t", "wchar_t", "__vcrt_bool"
}

class APIDocApp:
    def __init__(self, root):
        self.root = root
        self.root.title("C API Documentation Generator")
        self.selected_files = []
        self.symbols = []
        self.symbol_frames = []
        self.symbol_vars = {}
        self.generated_docs = []

        self.setup_gui()

    def setup_gui(self):
        self.drop_area = tk.Label(self.root, text="Drag or click to add files", bg="#ddd", relief=tk.RIDGE, padx=20, pady=10)
        self.drop_area.pack(padx=10, pady=10, fill=tk.X)
        self.drop_area.bind("<Button-1>", lambda e: self.add_files())

        self.canvas_frame = tk.Frame(self.root)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.canvas_frame)
        self.scrollbar = tk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.button_bar = tk.Frame(self.root)
        self.button_bar.pack(fill=tk.X, padx=10, pady=5)

        self.select_all_btn = tk.Button(self.button_bar, text="Select All", command=self.select_all)
        self.select_all_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.deselect_all_btn = tk.Button(self.button_bar, text="Deselect All", command=self.deselect_all)
        self.deselect_all_btn.pack(side=tk.LEFT)

        self.export_button = tk.Button(self.button_bar, text="Export", command=self.export_docs)
        self.export_button.pack(side=tk.LEFT, padx=(5, 10))

        self.generate_all_button = tk.Button(self.button_bar, text="Generate All Selected", command=self.start_generate_all)
        self.generate_all_button.pack(side=tk.RIGHT, padx=5)

        self.output_area = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, height=15)
        self.output_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

    def add_files(self):
        files = filedialog.askopenfilenames(filetypes=[("C Files", "*.c *.h")])
        if files:
            self.selected_files = list(files)
            self.list_symbols()

    def list_symbols(self):
        self.output_area.insert(tk.END, "\nScanning files...\n")
        self.symbols = []
        self.symbol_frames.clear()
        self.symbol_vars.clear()
        seen = set()
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        with tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix=".txt") as tmp:
            script_path = os.path.abspath("api_doc_tool.py")
            args = ["python", script_path, "--discover"] + self.selected_files
            result = subprocess.run(args, stdout=tmp, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            tmp.seek(0)
            for line in tmp:
                name = line.strip()
                if name and name not in seen:
                    seen.add(name)
                    self.symbols.append(name)

        for sym in self.symbols:
            frame = tk.Frame(self.scrollable_frame)
            var = tk.BooleanVar(value=sym not in STD_TYPES)
            chk = tk.Checkbutton(frame, text=sym, variable=var, anchor="w", width=60)
            chk.pack(side=tk.LEFT, anchor=tk.W)
            retry = tk.Button(frame, text="Retry", command=lambda s=sym: self.retry_symbol(s))
            retry.pack(side=tk.RIGHT)
            frame.pack(fill=tk.X, padx=10, pady=1)
            self.symbol_frames.append((sym, var, frame))
            self.symbol_vars[sym] = var

    def select_all(self):
        for var in self.symbol_vars.values():
            var.set(True)

    def deselect_all(self):
        for var in self.symbol_vars.values():
            var.set(False)

    def retry_symbol(self, symbol):
        self.output_area.insert(tk.END, f"Retrying generation for {symbol}...\n")
        threading.Thread(target=self._generate_single_symbol, args=(symbol,)).start()

    def _generate_single_symbol(self, symbol):
        with tempfile.NamedTemporaryFile(delete=False, mode='w+', suffix=".txt") as tmp:
            tmp.write(symbol + "\n")
            tmp.flush()
            script_path = os.path.abspath("api_doc_tool.py")
            args = ["python", script_path, "--generate", tmp.name] + self.selected_files
            result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr = result.stderr.decode('utf-8', errors='replace')
    
            cleaned = stdout.replace('```', '').split('Note that')[0].strip()
            if cleaned:
                section = f"<section>\n{cleaned}\n</section>\n"
                self.generated_docs.append(section)
                self.output_area.insert(tk.END, f"\nGenerated: {symbol}\n{cleaned}\n")
            else:
                self.output_area.insert(tk.END, f"\nGenerated: {symbol} (⚠️ No valid documentation returned)\n")
    
            if stderr:
                self.output_area.insert(tk.END, f"Errors while generating {symbol}:\n{stderr}\n")

    def start_generate_all(self):
        selected = [sym for sym, var, _ in self.symbol_frames if var.get()]
        self.output_area.insert(tk.END, f"Generating documentation for {len(selected)} symbols...\n")
        self.generated_docs.clear()
        threading.Thread(target=self._run_generation_thread, args=(selected,)).start()

    def _run_generation_thread(self, symbols):
        total = len(symbols)
        for i, symbol in enumerate(symbols, start=1):
            self.output_area.insert(tk.END, f"\n[{i}/{total}] Generating: {symbol}\n")
            self._generate_single_symbol(symbol)

    def export_docs(self):
        if not self.generated_docs:
            self.output_area.insert(tk.END, "\nNo documentation generated to export.\n")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML files", "*.html")])
        if filepath:
            html = """<!DOCTYPE html>
<html lang='en'>
<head>
    <meta charset='UTF-8'>
    <title>API Documentation</title>
    <style>
        body { font-family: sans-serif; padding: 2em; background: #f9f9f9; }
        h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }
        code { background: #eee; padding: 2px 4px; border-radius: 3px; }
        section { margin-bottom: 2em; background: #fff; padding: 1em; border: 1px solid #ccc; border-radius: 6px; }
    </style>
</head>
<body>
%s
</body>
</html>""" % ("".join(self.generated_docs))
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            self.output_area.insert(tk.END, f"\nExported documentation to {filepath}\n")

if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("800x600")
    app = APIDocApp(root)
    root.mainloop()
