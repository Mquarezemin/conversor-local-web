# -*- coding: utf-8 -*-
"""
Interface grafica simples para o converter.py.

Ela executa o conversor em subprocesso para manter a tela responsiva,
capturar logs e permitir cancelamento.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import ctypes
from tkinter import messagebox, ttk
from urllib.parse import unquote, urlparse

import pg8000


APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))

UI = {
    "bg": "#1e1e1e",
    "surface": "#252526",
    "surface_soft": "#2d2d30",
    "border": "#3c3c3c",
    "border_focus": "#6a9955",
    "text": "#cccccc",
    "muted": "#858585",
    "accent": "#6a9955",
    "accent_hover": "#7ba86a",
    "accent_pressed": "#567d46",
    "danger": "#c74e59",
    "danger_hover": "#d75a65",
    "button": "#2d2d30",
    "button_hover": "#3c3c3c",
    "input": "#1e1e1e",
    "selection": "#454545",
    "progress_trough": "#3c3c3c",
    "log_bg": "#1e1e1e",
    "log_fg": "#d4d4d4",
    "log_insert": "#ffffff",
    "ok": "#6a9955",
    "error": "#f14c4c",
}


def colorref(hex_color):
    """Converte #RRGGBB para COLORREF do Windows."""
    valor = str(hex_color).strip().lstrip("#")
    r = int(valor[0:2], 16)
    g = int(valor[2:4], 16)
    b = int(valor[4:6], 16)
    return r | (g << 8) | (b << 16)


def aplicar_barra_titulo_escura(janela):
    """Aplica tema escuro na barra nativa da janela no Windows 10/11."""
    if os.name != "nt":
        return
    try:
        janela.update_idletasks()
        hwnd = ctypes.c_void_p(janela.winfo_id())
        dwm = ctypes.windll.dwmapi

        modo_escuro = ctypes.c_int(1)
        for atributo in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            try:
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    atributo,
                    ctypes.byref(modo_escuro),
                    ctypes.sizeof(modo_escuro),
                )
            except Exception:
                pass

        for atributo, cor in (
            (35, UI["bg"]),      # DWMWA_CAPTION_COLOR
            (36, UI["text"]),    # DWMWA_TEXT_COLOR
            (34, UI["border"]),  # DWMWA_BORDER_COLOR
        ):
            try:
                valor = ctypes.c_uint(colorref(cor))
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    atributo,
                    ctypes.byref(valor),
                    ctypes.sizeof(valor),
                )
            except Exception:
                pass
    except Exception:
        pass


DEFAULTS = {
    "odbc_dsn": "giv",
    "odbc_user": "dba",
    "odbc_password": "sql",
    "pg_url": "",
    "pg_host": "rds-nuvem.ch0iy8mcu5f8.sa-east-1.rds.amazonaws.com",
    "pg_port": "5432",
    "pg_database": "desenvolvimento",
    "pg_user": "postgres",
    "pg_password": "tw-ApostS5202",
}

TABLE_OPTIONS = [
    ("2", "grupo", "grupo"),
    ("9", "departamento", "departamento"),
    ("8", "sub_grupo", "sub_grupo"),
    ("6", "marca", "marca"),
    ("7", "cor", "cor"),
    ("10", "tamanho", "tamanho"),
    ("11", "unidade", "unidade"),
    ("12", "colecao", "colecao"),
    ("13", "grade", "grade"),
    ("3", "fornecedor", "fornecedor"),
    ("5", "usuario", "usuario"),
    ("4", "cliente", "cliente"),
    ("15", "banco", "banco"),
    ("23", "banco_conta", "banco_conta"),
    ("16", "condicao_pagamento", "condicao_pagamento"),
    ("25", "cartao_administradora", "cartao_administradora"),
    ("26", "operacao_estoque", "operacao_estoque (historico)"),
    ("14", "produto", "produto"),
    ("27", "movimento_estoque", "movimento_estoque (historico de saldo/giro)"),
    ("24", "condicional", "condicional"),
    ("17", "pedido_compra", "pedido_compra"),
    ("18", "nota_fiscal_entrada", "nota_fiscal_entrada"),
    ("19", "prevenda", "prevenda"),
    ("20", "nota_fiscal_saida", "nota_fiscal_saida"),
    ("21", "titulo_receber", "titulo_receber"),
    ("22", "titulo_pagar", "titulo_pagar"),
    ("28", "caixa_movimentacao", "caixa_movto -> caixa_movimentacao (historico)"),
    ("29", "movimento_bancario", "banco_movto -> movimentos bancarios dos titulos"),
    ("30", "cliente_movto_credito", "cliente_movto_credito -> razao de creditos/devolucoes"),
]

TABLE_ORDER = {key: idx for idx, (_number, key, _label) in enumerate(TABLE_OPTIONS)}

DEPENDENCIES = {
    "sub_grupo": ("grupo",),
    "grade": ("tamanho",),
    "cliente": ("usuario",),
    "produto": (
        "grupo",
        "departamento",
        "sub_grupo",
        "marca",
        "cor",
        "tamanho",
        "unidade",
        "colecao",
        "grade",
        "fornecedor",
    ),
    "pedido_compra": ("fornecedor", "usuario", "condicao_pagamento", "produto"),
    "nota_fiscal_entrada": ("fornecedor", "usuario", "condicao_pagamento", "produto"),
    "prevenda": ("cliente", "usuario", "condicao_pagamento", "cartao_administradora", "produto"),
    "nota_fiscal_saida": ("cliente", "usuario", "condicao_pagamento", "produto"),
    "titulo_receber": ("cliente", "usuario", "condicao_pagamento", "nota_fiscal_saida"),
    "titulo_pagar": ("fornecedor", "usuario", "condicao_pagamento", "banco", "nota_fiscal_entrada"),
    "banco_conta": ("banco",),
    "condicional": ("cliente", "usuario", "produto"),
    "movimento_estoque": ("operacao_estoque", "produto", "usuario"),
    "caixa_movimentacao": ("cliente", "usuario"),
    "movimento_bancario": (
        "cliente",
        "fornecedor",
        "usuario",
        "banco_conta",
        "titulo_receber",
        "titulo_pagar",
    ),
    "cliente_movto_credito": ("cliente", "produto"),
}

REVERTER_DEPENDENTS = {}
for _child, _parents in DEPENDENCIES.items():
    for _parent in _parents:
        REVERTER_DEPENDENTS.setdefault(_parent, set()).add(_child)
REVERTER_DEPENDENTS.setdefault("nota_fiscal_saida", set()).add("prevenda")
REVERTER_DEPENDENTS.setdefault("pedido_compra", set()).add("nota_fiscal_entrada")
REVERTER_DEPENDENTS = {
    key: tuple(sorted(values, key=lambda item: TABLE_ORDER.get(item, 999)))
    for key, values in REVERTER_DEPENDENTS.items()
}

REVERTER_DIRECT_TABLE_KEYS = {
    "grupo": "grupo",
    "departamento": "departamento",
    "sub_grupo": "sub_grupo",
    "marca": "marca",
    "cor": "cor",
    "tamanho": "tamanho",
    "unidade": "unidade",
    "colecao": "colecao",
    "grade": "grade",
    "fornecedor": "fornecedor",
    "usuario": "usuario",
    "cliente": "cliente",
    "banco": "banco",
    "banco_conta": "banco_conta",
    "condicao_pagamento": "condicao_pagamento",
    "condicao_pagto": "condicao_pagamento",
    "condicao_pagamento_forma": "condicao_pagamento",
    "condicao_pagto_forma": "condicao_pagamento",
    "forma_pagamento": "condicao_pagamento",
    "cartao_administradora": "cartao_administradora",
    "operacao_estoque": "operacao_estoque",
    "produto": "produto",
    "produto_info": "produto",
    "produto_filho": "produto",
    "produto_colecao": "produto",
    "produto_preco": "produto",
    "produto_estoque": "produto",
    "produto_local_estoque": "produto",
    "movimento_estoque": "produto",
    "caixa_movimentacao": "caixa_movimentacao",
    "titulo_receber_movimento_bancario": "movimento_bancario",
    "titulo_pagar_movimento_bancario": "movimento_bancario",
    "inventario_estoque_item": "produto",
    "orcamento_item": "produto",
    "condicional": "condicional",
    "condicional_item": "condicional",
    "pedido_compra": "pedido_compra",
    "pedido_compra_item": "pedido_compra",
    "nota_fiscal_entrada": "nota_fiscal_entrada",
    "nota_fiscal_entrada_item": "nota_fiscal_entrada",
    "prevenda": "prevenda",
    "prevenda_item": "prevenda",
    "nota_fiscal_saida": "nota_fiscal_saida",
    "nota_fiscal_saida_item": "nota_fiscal_saida",
    "nota_fiscal_saida_info": "nota_fiscal_saida",
    "titulo_receber": "titulo_receber",
    "titulo_receber_historico": "titulo_receber",
    "titulo_pagar": "titulo_pagar",
    "titulo_pagar_historico": "titulo_pagar",
}

REVERTER_SEQUENCE_KEYS = {
    "grupo": "grupo",
    "departamento": "departamento",
    "sub_grupo": "sub_grupo",
    "marca": "marca",
    "cor": "cor",
    "tamanho": "tamanho",
    "unidade": "unidade",
    "colecao": "colecao",
    "grade": "grade",
    "fornecedor": "fornecedor",
    "usuario": "usuario",
    "cliente": "cliente",
    "banco": "banco",
    "banco_conta": "banco_conta",
    "condicao_pagamento": "condicao_pagamento",
    "condicao_pagto": "condicao_pagamento",
    "forma_pagamento": "condicao_pagamento",
    "cartao_administradora": "cartao_administradora",
    "operacao_estoque": "operacao_estoque",
    "movimento_estoque": "movimento_estoque",
    "produto": "produto",
    "condicional": "condicional",
    "pedido_compra": "pedido_compra",
    "nota_fiscal_entrada": "nota_fiscal_entrada",
    "prevenda": "prevenda",
    "nota_fiscal_saida": "nota_fiscal_saida",
    "titulo_receber": "titulo_receber",
    "titulo_pagar": "titulo_pagar",
}

TABLE_LABELS = {key: label for _number, key, label in TABLE_OPTIONS}
PROGRESS_LABELS = {
    key: label.split(" -> ")[-1]
    for _number, key, label in TABLE_OPTIONS
}
PROGRESS_LABELS["empresa"] = "empresa"

PROGRESS_KEYWORDS = [
    ("nota_fiscal_entrada", ("nota_fiscal_entrada", "nota fiscal entrada", "notas fiscais de entrada")),
    ("nota_fiscal_saida", ("nota_fiscal_saida", "nota fiscal saida", "notas fiscais de saida")),
    ("condicao_pagamento", ("condicao_pagamento", "condicao_pagto", "condicoes de pagamento")),
    ("titulo_receber", ("titulo_receber", "titulo receber", "contas a receber")),
    ("titulo_pagar", ("titulo_pagar", "titulo pagar", "contas a pagar")),
    ("banco_conta", ("banco_conta", "contas bancarias", "conta bancaria")),
    ("cartao_administradora", ("cartao_administradora", "administradoras de cartao", "administradora de cartao")),
    ("condicional", ("condicional", "condicionais", "orcamento_item", "orcamento")),
    ("pedido_compra", ("pedido_compra", "pedido compra", "pedidos de compra")),
    ("sub_grupo", ("sub_grupo", "subgrupo", "subgrupos")),
    ("departamento", ("departamento", "departamentos", "genero_moda")),
    ("fornecedor", ("fornecedor", "fornecedores")),
    ("usuario", ("usuario", "usuarios")),
    ("cliente", ("cliente", "clientes")),
    ("produto", ("produto", "produtos", "produto_preco", "produto_info")),
    ("banco", ("banco", "bancos")),
    ("grupo", ("grupo", "grupos")),
    ("marca", ("marca ", "marca:", "marcas")),
    ("cor", ("cor ", "cor:", "cores")),
    ("tamanho", ("tamanho", "tamanhos")),
    ("unidade", ("unidade", "unidades")),
    ("colecao", ("colecao", "colecoes")),
    ("grade", ("grade", "grades")),
    ("prevenda", ("prevenda", "prevendas")),
    ("cliente_movto_credito", ("cliente_movto_credito", "credito historico", "creditos/devolucoes")),
    ("empresa", ("empresa giv", "criando tenant", "criacao de empresa")),
]

ROTINAS_COM_PRODUTO = {
    "condicional",
    "pedido_compra",
    "nota_fiscal_entrada",
    "prevenda",
    "nota_fiscal_saida",
}


def worker_command(mode):
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, "-u", os.path.abspath(__file__), mode]


def run_converter_mode():
    import converter

    converter.main()


def test_giv_mode():
    import converter

    conn = converter.conectar_giv()
    conn.close()
    print("[OK] Teste GIV concluido.")


def test_web_mode():
    import converter

    conn = converter.conectar_web()
    conn.close()
    print("[OK] Teste Web concluido.")


class FieldDialog(tk.Toplevel):
    def __init__(self, master, title, fields, checkboxes=None, extra_buttons=None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.configure(bg=UI["bg"])
        self.after(10, lambda: aplicar_barra_titulo_escura(self))
        self.result = None
        self.vars = {}
        self.check_vars = {}

        body = ttk.Frame(self, padding=18, style="Card.TFrame")
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        for row, field in enumerate(fields):
            key = field["key"]
            ttk.Label(body, text=field["label"], style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=field.get("value", ""))
            show = "*" if field.get("secret") else ""
            entry = ttk.Entry(body, textvariable=var, width=46, show=show)
            entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(12, 0))
            self.vars[key] = var

        start_row = len(fields)
        if extra_buttons:
            extra_frame = ttk.Frame(body, style="Card.TFrame")
            extra_frame.grid(row=start_row, column=0, columnspan=2, sticky="ew", pady=(10, 4))
            for button in extra_buttons:
                command = button["command"]
                ttk.Button(
                    extra_frame,
                    text=button["label"],
                    command=lambda command=command: command(self),
                    style="Secondary.TButton",
                ).pack(side="left")
            start_row += 1

        for idx, check in enumerate(checkboxes or []):
            var = tk.BooleanVar(value=check.get("value", False))
            self.check_vars[check["key"]] = var
            ttk.Checkbutton(body, text=check["label"], variable=var, style="TCheckbutton").grid(
                row=start_row + idx,
                column=0,
                columnspan=2,
                sticky="w",
                pady=5,
            )

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=start_row + len(checkboxes or []), column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancelar", command=self.cancel, style="Secondary.TButton").pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="OK", command=self.ok, style="Accent.TButton").pack(side="right")

        self.bind("<Return>", lambda _event: self.ok())
        self.bind("<Escape>", lambda _event: self.cancel())
        self.transient(master)
        self.grab_set()
        self.after(50, lambda: self.focus_force())

    def ok(self):
        self.result = {key: var.get() for key, var in self.vars.items()}
        self.result.update({key: var.get() for key, var in self.check_vars.items()})
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class ReverterSelectionDialog(tk.Toplevel):
    def __init__(self, master, available_keys, command_counts):
        super().__init__(master)
        self.title("Selecionar reversao")
        self.geometry("520x620")
        self.minsize(480, 520)
        self.configure(bg=UI["bg"])
        self.after(10, lambda: aplicar_barra_titulo_escura(self))
        self.result = None
        self.available_keys = list(available_keys)
        self.available_set = set(self.available_keys)
        self.command_counts = command_counts
        self.vars = {}
        self.manual_keys = set()
        self.count_var = tk.StringVar()
        self.auto_var = tk.StringVar()

        body = ttk.Frame(self, padding=16, style="Card.TFrame")
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="Marque as tabelas que deseja reverter. Dependencias serao incluidas automaticamente.",
            style="Muted.TLabel",
            wraplength=470,
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(body, textvariable=self.count_var, style="Field.TLabel").pack(anchor="w", pady=(0, 8))

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Todas", command=self.select_all, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Nenhuma", command=self.clear_all, style="Secondary.TButton").pack(side="left")

        list_outer = ttk.Frame(body, style="Card.TFrame")
        list_outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            list_outer,
            bg=UI["surface"],
            highlightthickness=1,
            highlightbackground=UI["border"],
            bd=0,
        )
        scroll = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        list_frame = ttk.Frame(canvas, style="Card.TFrame")
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")

        def ajustar_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def ajustar_largura(event):
            canvas.itemconfigure(window_id, width=event.width)

        def rolar(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        list_frame.bind("<Configure>", ajustar_scroll)
        canvas.bind("<Configure>", ajustar_largura)
        canvas.bind("<MouseWheel>", rolar)
        list_frame.bind("<MouseWheel>", rolar)

        for idx, key in enumerate(self.available_keys, start=1):
            var = tk.BooleanVar(value=False)
            self.vars[key] = var
            qtd = self.command_counts.get(key, 0)
            label = f"{idx} - {TABLE_LABELS.get(key, key)} ({qtd} comandos)"
            chk = ttk.Checkbutton(
                list_frame,
                text=label,
                variable=var,
                command=lambda key=key: self.toggle_key(key),
                style="TCheckbutton",
            )
            chk.grid(row=idx - 1, column=0, sticky="w", padx=8, pady=3)
            chk.bind("<MouseWheel>", rolar)

        ttk.Label(
            body,
            textvariable=self.auto_var,
            style="Muted.TLabel",
            wraplength=470,
        ).pack(anchor="w", pady=(10, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Cancelar", command=self.cancel, style="Secondary.TButton").pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Reverter", command=self.ok, style="Danger.TButton").pack(side="right")

        self.apply_dependencies()
        self.bind("<Escape>", lambda _event: self.cancel())
        self.transient(master)
        self.grab_set()
        self.after(50, lambda: self.focus_force())

    def dependency_closure(self, keys):
        selected = set(keys)
        changed = True
        while changed:
            changed = False
            for key in list(selected):
                for dep in REVERTER_DEPENDENTS.get(key, ()):
                    if dep in self.available_set and dep not in selected:
                        selected.add(dep)
                        changed = True
        return selected

    def apply_dependencies(self):
        final_keys = self.dependency_closure(self.manual_keys)
        auto_keys = final_keys - self.manual_keys
        for key, var in self.vars.items():
            var.set(key in final_keys)
        self.count_var.set(f"Selecionadas: {len(final_keys)} / {len(self.available_keys)}")
        if auto_keys:
            nomes = ", ".join(TABLE_LABELS.get(key, key) for key in sorted(auto_keys, key=lambda item: TABLE_ORDER.get(item, 999)))
            self.auto_var.set(f"Incluidas automaticamente por dependencia: {nomes}")
        else:
            self.auto_var.set("Nenhuma dependencia automatica selecionada.")

    def toggle_key(self, key):
        if self.vars[key].get():
            self.manual_keys.add(key)
        else:
            self.manual_keys.discard(key)
        self.apply_dependencies()

    def select_all(self):
        self.manual_keys = set(self.available_keys)
        self.apply_dependencies()

    def clear_all(self):
        self.manual_keys.clear()
        self.apply_dependencies()

    def ok(self):
        selected = self.dependency_closure(self.manual_keys)
        if not selected:
            messagebox.showwarning("Reverter", "Selecione pelo menos uma tabela para reverter.")
            return
        self.result = {
            "manual_keys": set(self.manual_keys),
            "selected_keys": selected,
            "auto_added": selected - self.manual_keys,
        }
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class ConverterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Conversor GIV -> Web")
        self.geometry("1260x800")
        self.minsize(1060, 700)
        self.configure(bg=UI["bg"])
        self.apply_theme()

        self.log_queue = queue.Queue()
        self.process = None
        self.start_time = None
        self.total_mode_indeterminate = False
        self.awaiting_final_decision = False
        self.final_decision_sent = False
        self.table_vars = {}
        self.selected_count_var = tk.StringVar(value=f"Selecionados: 0 / {len(TABLE_OPTIONS)}")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.limite_produtos_var = tk.StringVar(value="")
        self.connection_status_vars = {}
        self.connection_status_labels = {}
        self.progress_task_keys = []
        self.progress_current_index = -1
        self.progress_current_key = None
        self.progress_item_value = 0
        self.progress_item_total = 0
        self.progress_marker_mode = False
        self.progress_completed_keys = set()

        self.create_widgets()
        self.after(10, lambda: aplicar_barra_titulo_escura(self))
        self.after(50, self.maximize_window)
        self.after(100, self.drain_log_queue)
        self.after(500, self.update_timer)

    def maximize_window(self):
        """Abre a interface maximizada no Windows."""
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass
        aplicar_barra_titulo_escura(self)

    def apply_theme(self):
        self.option_add("*Font", ("Segoe UI", 10))
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10), foreground=UI["text"])
        style.configure("App.TFrame", background=UI["bg"])
        style.configure("Card.TFrame", background=UI["surface"])
        style.configure("Soft.TFrame", background=UI["surface_soft"])

        style.configure("TLabel", background=UI["surface"], foreground=UI["text"])
        style.configure("Muted.TLabel", background=UI["surface"], foreground=UI["muted"])
        style.configure("Field.TLabel", background=UI["surface"], foreground=UI["muted"], font=("Segoe UI", 9, "bold"))

        style.configure(
            "Card.TLabelframe",
            background=UI["surface"],
            bordercolor=UI["border"],
            lightcolor=UI["border"],
            darkcolor=UI["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=UI["surface"],
            foreground=UI["text"],
            font=("Segoe UI Semibold", 10),
            padding=(6, 2),
        )

        style.configure(
            "TEntry",
            padding=(8, 6),
            fieldbackground=UI["input"],
            foreground=UI["text"],
            bordercolor=UI["border"],
            lightcolor=UI["border"],
            darkcolor=UI["border"],
            insertcolor=UI["text"],
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", UI["border_focus"])],
            lightcolor=[("focus", UI["border_focus"])],
            darkcolor=[("focus", UI["border_focus"])],
            fieldbackground=[("disabled", UI["surface_soft"]), ("readonly", UI["input"])],
            foreground=[("disabled", UI["muted"]), ("readonly", UI["text"])],
        )

        style.configure(
            "TButton",
            padding=(12, 7),
            background=UI["button"],
            foreground=UI["text"],
            bordercolor=UI["button"],
            lightcolor=UI["button"],
            darkcolor=UI["button"],
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "TButton",
            background=[("disabled", "#172033"), ("pressed", UI["button_hover"]), ("active", UI["button_hover"])],
            foreground=[("disabled", "#64748b"), ("pressed", UI["text"]), ("active", UI["text"])],
        )
        style.configure(
            "Secondary.TButton",
            background=UI["surface_soft"],
            bordercolor=UI["border"],
            lightcolor=UI["border"],
            darkcolor=UI["border"],
        )
        style.map("Secondary.TButton", background=[("active", UI["button_hover"]), ("pressed", UI["button_hover"])])
        style.configure(
            "Accent.TButton",
            background=UI["accent"],
            foreground="#ffffff",
            bordercolor=UI["accent"],
            lightcolor=UI["accent"],
            darkcolor=UI["accent"],
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", "#14524f"), ("pressed", UI["accent_pressed"]), ("active", UI["accent_hover"])],
            foreground=[("disabled", "#a7f3d0"), ("pressed", "#ffffff"), ("active", "#ffffff")],
        )
        style.configure(
            "Danger.TButton",
            background=UI["danger"],
            foreground="#ffffff",
            bordercolor=UI["danger"],
            lightcolor=UI["danger"],
            darkcolor=UI["danger"],
        )
        style.map(
            "Danger.TButton",
            background=[("disabled", "#f2b3ba"), ("pressed", UI["danger_hover"]), ("active", UI["danger_hover"])],
            foreground=[("disabled", "#fff1f2"), ("pressed", "#ffffff"), ("active", "#ffffff")],
        )

        style.configure("TCheckbutton", background=UI["surface"], foreground=UI["text"], padding=(2, 3))
        style.map(
            "TCheckbutton",
            background=[("active", UI["surface"])],
            foreground=[("active", UI["text"])],
            indicatorcolor=[("selected", UI["accent"]), ("!selected", UI["input"])],
        )

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=UI["progress_trough"],
            background=UI["accent"],
            bordercolor=UI["progress_trough"],
            lightcolor=UI["accent"],
            darkcolor=UI["accent"],
            thickness=13,
        )

        style.configure(
            "Treeview",
            background=UI["input"],
            fieldbackground=UI["input"],
            foreground=UI["text"],
            rowheight=28,
            bordercolor=UI["border"],
            lightcolor=UI["border"],
            darkcolor=UI["border"],
        )
        style.configure(
            "Treeview.Heading",
            background=UI["surface_soft"],
            foreground=UI["muted"],
            font=("Segoe UI Semibold", 9),
            relief="flat",
            padding=(8, 6),
        )
        style.map("Treeview", background=[("selected", UI["selection"])], foreground=[("selected", "#ffffff")])

    def create_widgets(self):
        main = ttk.Frame(self, padding=16, style="App.TFrame")
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main, style="App.TFrame")
        top.pack(fill="x")

        conn = ttk.LabelFrame(top, text="Conexoes", padding=(14, 12), style="Card.TLabelframe")
        conn.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.conn_vars = {key: tk.StringVar(value=value) for key, value in DEFAULTS.items()}

        labels = [
            ("ODBC DSN", "odbc_dsn"),
            ("ODBC usuario", "odbc_user"),
            ("ODBC senha", "odbc_password"),
            ("PostgreSQL URL", "pg_url"),
            ("PG host", "pg_host"),
            ("PG porta", "pg_port"),
            ("PG database", "pg_database"),
            ("PG usuario", "pg_user"),
            ("PG senha", "pg_password"),
        ]
        for idx, (label, key) in enumerate(labels):
            row = idx // 3
            col = (idx % 3) * 2
            ttk.Label(conn, text=label, style="Field.TLabel").grid(row=row, column=col, sticky="w", padx=6, pady=5)
            show = "*" if "senha" in label.lower() else ""
            ttk.Entry(conn, textvariable=self.conn_vars[key], width=34, show=show).grid(
                row=row,
                column=col + 1,
                sticky="ew",
                padx=6,
                pady=5,
            )
        for col in range(6):
            conn.columnconfigure(col, weight=1)

        actions = ttk.LabelFrame(top, text="Acoes", padding=(12, 12), style="Card.TLabelframe")
        actions.pack(side="right", fill="y")
        self.add_test_action(actions, "Testar GIV", "--test-giv")
        self.add_test_action(actions, "Testar Web", "--test-web")
        ttk.Button(actions, text="Criar Empresa", command=self.create_company, style="Accent.TButton").pack(fill="x", padx=2, pady=(8, 5))
        ttk.Button(actions, text="Empresa do GIV", command=self.import_company_giv, style="Secondary.TButton").pack(fill="x", padx=2, pady=5)

        middle = ttk.Frame(main, style="App.TFrame")
        middle.pack(fill="both", expand=True, pady=(14, 0))

        tables = ttk.LabelFrame(middle, text="Tabelas", padding=(12, 10), style="Card.TLabelframe")
        tables.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(tables, textvariable=self.selected_count_var, style="Muted.TLabel").pack(
            anchor="w",
            padx=2,
            pady=(0, 8),
        )
        btns = ttk.Frame(tables, style="Card.TFrame")
        btns.pack(fill="x", padx=2, pady=(0, 8))
        ttk.Button(btns, text="Todas", command=self.select_all_tables, style="Secondary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Nenhuma", command=self.clear_tables, style="Secondary.TButton").pack(side="left")
        list_outer = ttk.Frame(tables, style="Card.TFrame")
        list_outer.pack(fill="both", expand=True, padx=2, pady=2)
        list_canvas = tk.Canvas(
            list_outer,
            bg=UI["surface"],
            highlightthickness=0,
            bd=0,
            width=360,
        )
        list_scroll = ttk.Scrollbar(list_outer, orient="vertical", command=list_canvas.yview)
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_canvas.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")

        list_frame = ttk.Frame(list_canvas, style="Card.TFrame")
        list_window = list_canvas.create_window((0, 0), window=list_frame, anchor="nw")

        def _ajustar_scroll_tabelas(_event=None):
            list_canvas.configure(scrollregion=list_canvas.bbox("all"))

        def _ajustar_largura_lista(event):
            list_canvas.itemconfigure(list_window, width=event.width)

        def _rolar_tabelas(event):
            list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        list_frame.bind("<Configure>", _ajustar_scroll_tabelas)
        list_canvas.bind("<Configure>", _ajustar_largura_lista)
        list_canvas.bind("<MouseWheel>", _rolar_tabelas)
        list_frame.bind("<MouseWheel>", _rolar_tabelas)
        for idx, (number, key, label) in enumerate(TABLE_OPTIONS):
            var = tk.BooleanVar(value=False)
            self.table_vars[key] = (number, var)
            chk = ttk.Checkbutton(
                list_frame,
                text=f"{number} - {label}",
                variable=var,
                command=self.update_selected_count,
            )
            chk.grid(
                row=idx,
                column=0,
                sticky="w",
                pady=2,
            )
            chk.bind("<MouseWheel>", _rolar_tabelas)

        right = ttk.Frame(middle, style="App.TFrame")
        right.pack(side="left", fill="both", expand=True)

        controls = ttk.Frame(right, style="App.TFrame")
        controls.pack(fill="x")
        self.start_btn = ttk.Button(controls, text="Comecar", command=self.start_conversion, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn = ttk.Button(controls, text="Cancelar", command=self.cancel_process, state="disabled", style="Danger.TButton")
        self.cancel_btn.pack(side="left", padx=(0, 8))
        self.reverter_btn = ttk.Button(controls, text="Reverter", command=self.run_reverter, style="Secondary.TButton")
        self.reverter_btn.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Limpar logs", command=self.clear_logs, style="Secondary.TButton").pack(side="left")

        # Modos de execucao segura (equivalentes a --dry-run e --limit-products).
        seguranca = ttk.Frame(right, style="App.TFrame")
        seguranca.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(
            seguranca,
            text="Simulacao (dry-run): valida tudo e termina em ROLLBACK",
            variable=self.dry_run_var,
        ).pack(side="left", padx=(0, 16))
        ttk.Label(seguranca, text="Limitar produtos:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(seguranca, textvariable=self.limite_produtos_var, width=8).pack(side="left")
        ttk.Label(
            seguranca,
            text="(quantidade de produtos raiz; vazio = todos)",
            style="Muted.TLabel",
        ).pack(side="left", padx=(6, 0))

        progress = ttk.LabelFrame(right, text="Progresso", padding=(12, 10), style="Card.TLabelframe")
        progress.pack(fill="x", pady=(12, 12))
        self.item_label = tk.StringVar(value="Tabela atual: -")
        self.total_label = tk.StringVar(value="Total da conversao: parado")
        self.time_label = tk.StringVar(value="Tempo: 00:00:00")
        ttk.Label(progress, textvariable=self.item_label, style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=2, pady=(0, 6))
        ttk.Label(progress, textvariable=self.time_label, style="Muted.TLabel").grid(row=0, column=1, sticky="e", padx=2, pady=(0, 6))
        self.item_bar = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.item_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=(0, 10))
        ttk.Label(progress, textvariable=self.total_label, style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", padx=2, pady=(0, 6))
        self.total_bar = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.total_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=2, pady=(0, 2))
        progress.columnconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(right, text="Logs", padding=(12, 10), style="Card.TLabelframe")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            height=18,
            bg=UI["log_bg"],
            fg=UI["log_fg"],
            insertbackground=UI["log_insert"],
            selectbackground=UI["selection"],
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def add_test_action(self, parent, text, mode):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", padx=2, pady=5)
        ttk.Button(row, text=text, command=lambda: self.run_test(mode), style="Secondary.TButton").pack(side="left", fill="x", expand=True)
        var = tk.StringVar(value="-")
        self.connection_status_vars[mode] = var
        label = tk.Label(
            row,
            textvariable=var,
            width=3,
            fg=UI["muted"],
            bg=UI["surface_soft"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            borderwidth=0,
        )
        label.pack(side="left", padx=(8, 0), ipady=5)
        self.connection_status_labels[mode] = label

    def set_connection_status(self, mode, status):
        var = self.connection_status_vars.get(mode)
        label = self.connection_status_labels.get(mode)
        if not var or not label:
            return
        if status == "testing":
            var.set("...")
            label.configure(fg=UI["muted"], bg=UI["surface_soft"])
        elif status == "ok":
            var.set("V")
            label.configure(fg="#ffffff", bg=UI["ok"])
        elif status == "error":
            var.set("X")
            label.configure(fg="#ffffff", bg=UI["error"])
        else:
            var.set("-")
            label.configure(fg=UI["muted"], bg=UI["surface_soft"])

    def env_for_process(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["CONVERTER_OUTPUT_DIR"] = APP_DIR
        mapping = {
            "CONVERTER_ODBC_DSN": "odbc_dsn",
            "CONVERTER_ODBC_USER": "odbc_user",
            "CONVERTER_ODBC_PASSWORD": "odbc_password",
            "CONVERTER_PG_URL": "pg_url",
            "CONVERTER_PG_HOST": "pg_host",
            "CONVERTER_PG_PORT": "pg_port",
            "CONVERTER_PG_DATABASE": "pg_database",
            "CONVERTER_PG_USER": "pg_user",
            "CONVERTER_PG_PASSWORD": "pg_password",
        }
        for env_name, key in mapping.items():
            value = self.conn_vars[key].get().strip()
            if value:
                env[env_name] = value
            elif env_name in env:
                env.pop(env_name, None)

        # Modos de execucao segura lidos por converter.parse_argumentos_execucao().
        if self.dry_run_var.get():
            env["CONVERTER_DRY_RUN"] = "1"
        else:
            env.pop("CONVERTER_DRY_RUN", None)

        limite = self.limite_produtos_var.get().strip()
        if limite.isdigit() and int(limite) > 0:
            env["CONVERTER_LIMIT_PRODUCTS"] = limite
        else:
            env.pop("CONVERTER_LIMIT_PRODUCTS", None)
        return env

    def selected_tables(self):
        selected = []
        selected_keys = []
        for key, (number, var) in self.table_vars.items():
            if var.get():
                selected.append(number)
                selected_keys.append(key)
        return selected, selected_keys

    def update_selected_count(self):
        total = len(self.table_vars)
        selecionados = sum(1 for _number, var in self.table_vars.values() if var.get())
        self.selected_count_var.set(f"Selecionados: {selecionados} / {total}")

    def missing_dependencies(self, selected_keys):
        selected = set(selected_keys)
        missing = {}
        for key in selected_keys:
            deps = [dep for dep in DEPENDENCIES.get(key, ()) if dep not in selected]
            if deps:
                missing[key] = deps
        return missing

    def confirm_missing_dependencies(self, selected_keys):
        missing = self.missing_dependencies(selected_keys)
        if not missing:
            return True
        lines = [
            "Algumas tabelas selecionadas dependem de cadastros que nao foram marcados.",
            "",
            "O conversor vai tentar usar o que ja existe no Web. Se o banco estiver limpo, marque tambem:",
            "",
        ]
        for key, deps in missing.items():
            deps_text = ", ".join(TABLE_LABELS.get(dep, dep) for dep in deps)
            lines.append(f"- {TABLE_LABELS.get(key, key)} depende de: {deps_text}")
        lines.append("")
        lines.append("Continuar mesmo assim?")
        return messagebox.askyesno("Dependencias nao marcadas", "\n".join(lines))

    def select_all_tables(self):
        for _number, var in self.table_vars.values():
            var.set(True)
        self.update_selected_count()

    def clear_tables(self):
        for _number, var in self.table_vars.values():
            var.set(False)
        self.update_selected_count()

    def pg_connection_params(self):
        config = {
            "host": self.conn_vars["pg_host"].get().strip(),
            "port": self.conn_vars["pg_port"].get().strip() or "5432",
            "database": self.conn_vars["pg_database"].get().strip(),
            "user": self.conn_vars["pg_user"].get().strip(),
            "password": self.conn_vars["pg_password"].get().strip(),
        }
        url = self.conn_vars["pg_url"].get().strip()
        if url:
            parsed = urlparse(url)
            if parsed.hostname:
                config["host"] = parsed.hostname
            if parsed.port:
                config["port"] = str(parsed.port)
            if parsed.path and parsed.path.strip("/"):
                config["database"] = parsed.path.strip("/").split("/")[0]
            if parsed.username:
                config["user"] = unquote(parsed.username)
            if parsed.password:
                config["password"] = unquote(parsed.password)
        config["port"] = int(config["port"])
        return config

    def carregar_sql_reverter(self):
        caminho = os.path.join(APP_DIR, "reverter.txt")
        if not os.path.exists(caminho):
            raise FileNotFoundError(f"Arquivo nao encontrado: {caminho}")
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return caminho, arquivo.read()

    def dividir_sql_reverter(self, sql):
        comandos = []
        atual = []
        em_string = False
        i = 0
        while i < len(sql):
            ch = sql[i]
            prox = sql[i + 1] if i + 1 < len(sql) else ""
            if not em_string and ch == "-" and prox == "-":
                while i < len(sql) and sql[i] not in "\r\n":
                    i += 1
                continue
            if ch == "'":
                atual.append(ch)
                if em_string and prox == "'":
                    atual.append(prox)
                    i += 2
                    continue
                em_string = not em_string
                i += 1
                continue
            if ch == ";" and not em_string:
                comando = "".join(atual).strip()
                if comando:
                    comandos.append(comando)
                atual = []
                i += 1
                continue
            atual.append(ch)
            i += 1
        comando = "".join(atual).strip()
        if comando:
            comandos.append(comando)
        return comandos

    def tabela_delete_reverter(self, comando):
        match = re.search(
            r'^\s*DELETE\s+FROM\s+((?:"[^"]+"\.)?"[^"]+"|(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*)',
            comando,
            re.IGNORECASE,
        )
        if not match:
            return None
        nome = match.group(1).strip()
        partes = [parte.strip().strip('"') for parte in nome.split(".")]
        return partes[-1].lower() if partes else None

    def key_sequence_reverter(self, comando):
        match = re.search(r"setval\s*\(\s*'([^']+)'::regclass", comando, re.IGNORECASE)
        if not match:
            return None
        sequence = match.group(1).split(".")[-1].lower()
        for prefixo, key in sorted(REVERTER_SEQUENCE_KEYS.items(), key=lambda item: len(item[0]), reverse=True):
            if sequence.startswith(prefixo + "_"):
                return key
        return None

    def classificar_comando_reverter(self, comando):
        texto = comando.lower()
        if texto in ("begin", "commit", "rollback"):
            return set()

        key_sequence = self.key_sequence_reverter(comando)
        if key_sequence:
            return {key_sequence}

        tabela = self.tabela_delete_reverter(comando)
        if not tabela:
            return set()

        # Comandos de seguranca gerados por referencia a cadastros convertidos.
        # Ex.: apagar itens/documentos que apontam para cd_produto, cd_cliente ou cd_fornecedor novo.
        if '"cd_produto"' in texto:
            return {"produto"}
        if '"cd_cliente"' in texto or '"clientecd_cliente"' in texto:
            return {"cliente"}
        if '"cd_fornecedor"' in texto or '"cd_transportador"' in texto:
            return {"fornecedor"}
        if '"cd_administradora"' in texto:
            return {"cartao_administradora"}
        if tabela == "banco_conta" and '"cd_banco"' in texto:
            return {"banco"}
        if tabela == "grade_tamanho":
            keys = set()
            if '"a"' in texto:
                keys.add("grade")
            if '"b"' in texto:
                keys.add("tamanho")
            return keys or {"grade"}

        key = REVERTER_DIRECT_TABLE_KEYS.get(tabela)
        return {key} if key else set()

    def analisar_reverter_sql(self, sql):
        comandos = self.dividir_sql_reverter(sql)
        entradas = []
        contagens = {}
        for comando in comandos:
            keys = self.classificar_comando_reverter(comando)
            entradas.append((comando, keys))
            for key in keys:
                if key in TABLE_LABELS:
                    contagens[key] = contagens.get(key, 0) + 1
        return entradas, contagens

    def comandos_reverter_parcial(self, entradas, selected_keys):
        selected = set(selected_keys)
        comandos = []
        deletes = 0
        for comando, keys in entradas:
            if comando.strip().lower() in ("begin", "commit", "rollback"):
                continue
            if keys & selected:
                comandos.append(comando)
                if self.tabela_delete_reverter(comando):
                    deletes += 1
        if not comandos:
            raise RuntimeError("Nenhum comando encontrado para as tabelas selecionadas.")
        return ["BEGIN"] + comandos + ["COMMIT"], deletes

    def run_reverter(self):
        if self.process_running():
            messagebox.showwarning("Reverter", "Aguarde a conversao atual terminar antes de reverter.")
            return
        try:
            caminho, sql = self.carregar_sql_reverter()
            entradas, contagens = self.analisar_reverter_sql(sql)
        except Exception as exc:
            messagebox.showerror("Reverter", str(exc))
            return
        available_keys = [
            key
            for _number, key, _label in TABLE_OPTIONS
            if contagens.get(key, 0) > 0
        ]
        if not available_keys:
            messagebox.showwarning("Reverter", "Nao encontrei tabelas reversiveis no reverter.txt.")
            return

        dialog = ReverterSelectionDialog(self, available_keys, contagens)
        self.wait_window(dialog)
        if not dialog.result:
            return

        selected_keys = dialog.result["selected_keys"]
        auto_added = dialog.result["auto_added"]
        manual_keys = dialog.result["manual_keys"]
        try:
            comandos, deletes = self.comandos_reverter_parcial(entradas, selected_keys)
        except Exception as exc:
            messagebox.showerror("Reverter", str(exc))
            return

        selecionadas = ", ".join(
            TABLE_LABELS.get(key, key)
            for key in sorted(selected_keys, key=lambda item: TABLE_ORDER.get(item, 999))
        )
        mensagem = [
            "Executar reversao parcial no banco Web configurado?",
            "",
            f"Arquivo: {caminho}",
            f"Tabelas selecionadas: {selecionadas}",
            f"Comandos SQL: {len(comandos)} ({deletes} DELETEs)",
        ]
        if auto_added:
            auto_txt = ", ".join(
                TABLE_LABELS.get(key, key)
                for key in sorted(auto_added, key=lambda item: TABLE_ORDER.get(item, 999))
            )
            mensagem.extend([
                "",
                "As seguintes tabelas foram incluidas automaticamente por dependencia:",
                auto_txt,
            ])
        if not messagebox.askyesno("Executar reversao", "\n".join(mensagem)):
            return

        manuais_txt = ", ".join(
            TABLE_LABELS.get(key, key)
            for key in sorted(manual_keys, key=lambda item: TABLE_ORDER.get(item, 999))
        )
        auto_log = ""
        if auto_added:
            auto_log = "; automaticas: " + ", ".join(
                TABLE_LABELS.get(key, key)
                for key in sorted(auto_added, key=lambda item: TABLE_ORDER.get(item, 999))
            )
        self.log(f"\n[REVERTER] Executando reversao parcial de {caminho}...\n")
        self.log(f"[REVERTER] Marcadas: {manuais_txt or '-'}{auto_log}\n")
        self.log(f"[REVERTER] {len(comandos)} comandos preparados ({deletes} DELETEs).\n")
        self.reverter_btn.configure(state="disabled")
        threading.Thread(target=self.run_reverter_thread, args=(comandos,), daemon=True).start()

    def run_reverter_thread(self, comandos_sql):
        try:
            comandos = self.dividir_sql_reverter(comandos_sql) if isinstance(comandos_sql, str) else list(comandos_sql)
            if not comandos:
                raise RuntimeError("O arquivo reverter.txt nao possui comandos SQL.")
            conn = pg8000.connect(**self.pg_connection_params())
            cursor = conn.cursor()
            try:
                for idx, comando in enumerate(comandos, start=1):
                    cursor.execute(comando)
                    if idx % 20 == 0 or idx == len(comandos):
                        self.log_queue.put(("log", f"[REVERTER] {idx}/{len(comandos)} comandos executados...\n"))
                try:
                    conn.commit()
                except Exception:
                    pass
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cursor.close()
                conn.close()
            self.log_queue.put(("log", "[OK] Reversao executada com sucesso.\n"))
            self.log_queue.put(("reverter_done", True))
        except Exception as exc:
            self.log_queue.put(("log", f"[ERRO] Falha ao executar reverter.txt: {exc}\n"))
            self.log_queue.put(("reverter_done", False))

    def ask_dialog(self, title, fields, checkboxes=None, extra_buttons=None):
        dialog = FieldDialog(self, title, fields, checkboxes, extra_buttons)
        self.wait_window(dialog)
        return dialog.result

    @staticmethod
    def quote_identificador(nome):
        return '"' + str(nome).replace('"', '""') + '"'

    def pg_config_from_fields(self):
        config = {
            "host": self.conn_vars["pg_host"].get().strip(),
            "port": self.conn_vars["pg_port"].get().strip() or "5432",
            "database": self.conn_vars["pg_database"].get().strip(),
            "user": self.conn_vars["pg_user"].get().strip(),
            "password": self.conn_vars["pg_password"].get(),
        }
        url = self.conn_vars["pg_url"].get().strip()
        if url:
            parsed = urlparse(url)
            if parsed.hostname:
                config["host"] = parsed.hostname
            if parsed.port:
                config["port"] = str(parsed.port)
            if parsed.path and parsed.path.strip("/"):
                config["database"] = parsed.path.strip("/").split("/")[0]
            if parsed.username:
                config["user"] = unquote(parsed.username)
            if parsed.password:
                config["password"] = unquote(parsed.password)
        return config

    def resolver_tabela_web_view(self, cursor, nome_tabela):
        cursor.execute(
            """
            SELECT table_schema, table_name
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               AND lower(table_name) = lower(%s)
             ORDER BY
               CASE
                 WHEN table_name = %s THEN 0
                 WHEN table_name = lower(%s) THEN 1
                 ELSE 2
               END
             LIMIT 1
            """,
            (nome_tabela, nome_tabela, nome_tabela),
        )
        row = cursor.fetchone()
        if row:
            return f"{self.quote_identificador(row[0])}.{self.quote_identificador(row[1])}"
        return self.quote_identificador(nome_tabela)

    def buscar_empresas_web(self):
        config = self.pg_config_from_fields()
        conn = pg8000.connect(
            host=config["host"],
            port=int(config["port"]),
            database=config["database"],
            user=config["user"],
            password=config["password"],
        )
        try:
            conn.autocommit = True
            cursor = conn.cursor()
            tabela_tenant = self.resolver_tabela_web_view(cursor, "tenant")
            tabela_empresa = self.resolver_tabela_web_view(cursor, "empresa")
            cursor.execute(
                f"""
                SELECT
                    t.{self.quote_identificador('id')} AS tenant_id,
                    t.{self.quote_identificador('name')} AS tenant_name,
                    e.{self.quote_identificador('cd_empresa')} AS cd_empresa,
                    e.{self.quote_identificador('nm_empresa')} AS nm_empresa,
                    e.{self.quote_identificador('cnpj')} AS cnpj
                  FROM {tabela_tenant} t
                  LEFT JOIN {tabela_empresa} e
                    ON e.{self.quote_identificador('tenant_id')} = t.{self.quote_identificador('id')}
                 ORDER BY t.{self.quote_identificador('id')}, e.{self.quote_identificador('cd_empresa')}
                """
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def show_web_empresas(self, parent=None):
        janela_ref = parent if parent is not None else self
        try:
            janela_ref.configure(cursor="watch")
            self.configure(cursor="watch")
            self.update_idletasks()
            rows = self.buscar_empresas_web()
        except Exception as exc:
            messagebox.showerror("Tenants / Empresas Web", f"Nao foi possivel consultar o banco Web:\n\n{exc}")
            return
        finally:
            try:
                janela_ref.configure(cursor="")
                self.configure(cursor="")
            except Exception:
                pass

        view = tk.Toplevel(self)
        view.title("Tenants / Empresas Web")
        view.geometry("860x420")
        view.minsize(720, 320)
        view.configure(bg=UI["bg"])
        view.after(10, lambda: aplicar_barra_titulo_escura(view))
        view.transient(janela_ref)

        frame = ttk.Frame(view, padding=16, style="Card.TFrame")
        frame.pack(fill="both", expand=True)

        columns = ("tenant_id", "tenant_name", "cd_empresa", "nm_empresa", "cnpj")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        headings = {
            "tenant_id": "Tenant ID",
            "tenant_name": "Tenant",
            "cd_empresa": "CD Empresa",
            "nm_empresa": "Empresa",
            "cnpj": "CNPJ",
        }
        widths = {
            "tenant_id": 80,
            "tenant_name": 210,
            "cd_empresa": 90,
            "nm_empresa": 260,
            "cnpj": 150,
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="w")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for row in rows:
            tree.insert("", "end", values=[("" if value is None else str(value)) for value in row])

        footer = ttk.Frame(view, padding=(16, 0, 16, 16), style="Card.TFrame")
        footer.pack(fill="x")
        ttk.Label(footer, text=f"{len(rows)} registro(s) encontrado(s)", style="Muted.TLabel").pack(side="left")

        def fechar():
            try:
                view.grab_release()
            except Exception:
                pass
            view.destroy()
            if parent is not None and parent.winfo_exists():
                parent.grab_set()
                parent.focus_force()

        ttk.Button(footer, text="Fechar", command=fechar, style="Secondary.TButton").pack(side="right")
        view.protocol("WM_DELETE_WINDOW", fechar)
        view.grab_set()

    def inserir_tenant_web(self, valores):
        nome = (valores.get("tenant_name") or "").strip()
        if not nome:
            raise ValueError("Informe o nome do tenant.")
        tenancy_type = (valores.get("tenancy_type") or "SHARED").strip() or "SHARED"
        database_url = (valores.get("database_url") or "").strip() or None

        config = self.pg_config_from_fields()
        conn = pg8000.connect(
            host=config["host"],
            port=int(config["port"]),
            database=config["database"],
            user=config["user"],
            password=config["password"],
        )
        try:
            conn.autocommit = False
            cursor = conn.cursor()
            tabela_tenant = self.resolver_tabela_web_view(cursor, "tenant")
            cursor.execute(
                f"""
                INSERT INTO {tabela_tenant}
                    ({self.quote_identificador('name')},
                     {self.quote_identificador('tenancyType')},
                     {self.quote_identificador('databaseUrl')})
                VALUES (%s, %s, %s)
                RETURNING {self.quote_identificador('id')}
                """,
                (nome, tenancy_type, database_url),
            )
            tenant_id = cursor.fetchone()[0]
            conn.commit()
            return tenant_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def create_tenant_modal(self, parent_dialog=None):
        parent = parent_dialog if parent_dialog is not None else self
        fields = [
            {"key": "tenant_name", "label": "Nome do tenant", "value": ""},
            {"key": "tenancy_type", "label": "Tipo tenancy", "value": "SHARED"},
            {"key": "database_url", "label": "Database URL", "value": ""},
        ]
        dialog = FieldDialog(parent, "Criar tenant", fields)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            parent.configure(cursor="watch")
            self.configure(cursor="watch")
            self.update_idletasks()
            tenant_id = self.inserir_tenant_web(dialog.result)
        except Exception as exc:
            messagebox.showerror("Criar tenant", f"Nao foi possivel criar o tenant:\n\n{exc}")
            return
        finally:
            try:
                parent.configure(cursor="")
                self.configure(cursor="")
            except Exception:
                pass

        if parent_dialog is not None and "tenant_id" in parent_dialog.vars:
            parent_dialog.vars["tenant_id"].set(str(tenant_id))
            try:
                parent_dialog.grab_set()
                parent_dialog.focus_force()
            except Exception:
                pass
        messagebox.showinfo("Criar tenant", f"Tenant criado com sucesso.\n\nTENANT_ID: {tenant_id}")

    def ask_commit(self):
        return messagebox.askyesno(
            "Confirmar no final",
            "A conversao terminou e a transacao esta aberta.\n\n"
            "Deseja fazer COMMIT agora?\n\n"
            "Sim = confirma no banco Web.\nNao = faz ROLLBACK/teste.",
        )

    def start_conversion(self):
        if self.process_running():
            return
        selected, selected_keys = self.selected_tables()
        if not selected:
            messagebox.showwarning("Tabelas", "Selecione pelo menos uma tabela.")
            return
        if not self.confirm_missing_dependencies(selected_keys):
            return

        fields = [
            {"key": "tenant_id", "label": "TENANT_ID Web", "value": "1"},
            {"key": "cd_empresa", "label": "CD_EMPRESA Web", "value": "1"},
            {"key": "cd_empresa_giv", "label": "CD_EMPRESA GIV", "value": "1"},
        ]

        checkboxes = []
        precisa_api_cep = "fornecedor" in selected_keys or "cliente" in selected_keys
        if precisa_api_cep:
            checkboxes.append({"key": "usar_api_cep", "label": "Usar API de CEP", "value": False})

        extra_buttons = [
            {"label": "Ver tenants/empresas Web", "command": self.show_web_empresas},
        ]
        values = self.ask_dialog("Parametros da conversao", fields, checkboxes, extra_buttons)
        if values is None:
            return

        inputs = [
            ",".join(selected),
            values["tenant_id"],
            values["cd_empresa"],
            values["cd_empresa_giv"],
        ]
        if precisa_api_cep:
            inputs.append("S" if values.get("usar_api_cep") else "N")
        self.start_worker(inputs, selected_keys=selected_keys)

    def create_company(self):
        if self.process_running():
            return
        fields = [
            {"key": "nm_empresa", "label": "Razao social / nome", "value": ""},
            {"key": "nm_fantasia", "label": "Nome fantasia", "value": ""},
            {"key": "nm_reduzido", "label": "Nome reduzido", "value": ""},
            {"key": "tenant_id", "label": "TENANT_ID Web", "value": ""},
            {"key": "cidade", "label": "Cidade Web (IBGE ou nome)", "value": ""},
            {"key": "cep", "label": "CEP", "value": ""},
            {"key": "endereco", "label": "Endereco", "value": "S/N"},
            {"key": "numero", "label": "Numero", "value": "S/N"},
            {"key": "bairro", "label": "Bairro", "value": "CENTRO"},
            {"key": "cnpj", "label": "CNPJ", "value": ""},
            {"key": "ie", "label": "Inscricao estadual", "value": ""},
            {"key": "im", "label": "Inscricao municipal", "value": ""},
            {"key": "cnae", "label": "CNAE", "value": ""},
            {"key": "fone", "label": "Fone", "value": ""},
            {"key": "celular", "label": "Celular", "value": ""},
            {"key": "email", "label": "Email", "value": ""},
            {"key": "dias", "label": "Dias de licenca", "value": "30"},
            {"key": "admin_nome", "label": "Nome admin", "value": "Administrador"},
            {"key": "admin_login", "label": "Login admin", "value": "admin"},
            {"key": "admin_senha", "label": "Senha admin", "value": "admin123", "secret": True},
        ]
        extra_buttons = [
            {"label": "Criar tenant", "command": self.create_tenant_modal},
            {"label": "Ver tenants/empresas Web", "command": self.show_web_empresas},
        ]
        values = self.ask_dialog("Criar Empresa", fields, extra_buttons=extra_buttons)
        if values is None:
            return
        inputs = [
            "0",
            values["nm_empresa"],
            values["nm_fantasia"] or values["nm_empresa"],
            values["nm_reduzido"],
            values["tenant_id"],
            values["cidade"],
            values["cep"],
            values["endereco"],
            values["numero"],
            values["bairro"],
            values["cnpj"],
            values["ie"],
            values["im"],
            values["cnae"],
            values["fone"],
            values["celular"],
            values["email"],
            values["dias"],
            values["admin_nome"],
            values["admin_login"],
            values["admin_senha"],
        ]
        self.start_worker(inputs, selected_keys=["empresa"])

    def import_company_giv(self):
        if self.process_running():
            return
        fields = [
            {"key": "cd_empresa_giv", "label": "CD_EMPRESA no GIV", "value": "1"},
            {"key": "tenant_id", "label": "TENANT_ID Web", "value": ""},
            {"key": "cidade_fallback", "label": "Cidade Web reserva (IBGE/nome, se precisar)", "value": ""},
            {"key": "dias", "label": "Dias de licenca", "value": "30"},
            {"key": "admin_nome", "label": "Nome admin", "value": "Administrador"},
            {"key": "admin_login", "label": "Login admin", "value": "admin"},
            {"key": "admin_senha", "label": "Senha admin", "value": "admin123", "secret": True},
        ]
        extra_buttons = [
            {"label": "Criar tenant", "command": self.create_tenant_modal},
            {"label": "Ver tenants/empresas Web", "command": self.show_web_empresas},
        ]
        values = self.ask_dialog("Empresa do GIV", fields, extra_buttons=extra_buttons)
        if values is None:
            return
        inputs = [
            "1",
            values["cd_empresa_giv"],
            values["tenant_id"],
            values["dias"],
            values["admin_nome"],
            values["admin_login"],
            values["admin_senha"],
        ]
        extra_env = {
            "CONVERTER_EMPRESA_GIV_CIDADE_FALLBACK": values.get("cidade_fallback", ""),
        }
        self.start_worker(inputs, extra_env, selected_keys=["empresa"])

    def start_worker(self, inputs, extra_env=None, selected_keys=None):
        self.clear_logs()
        self.start_time = time.monotonic()
        self.set_running(True)
        self.init_progress(selected_keys)
        self.awaiting_final_decision = False
        self.final_decision_sent = False

        cmd = worker_command("--run-converter")
        env = self.env_for_process()
        for key, value in (extra_env or {}).items():
            value = str(value).strip()
            if value:
                env[key] = value
            else:
                env.pop(key, None)
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=APP_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
            )
            payload = "\n".join(str(item) for item in inputs) + "\n"
            self.process.stdin.write(payload)
            self.process.stdin.flush()
        except Exception as exc:
            self.set_running(False)
            self.total_label.set("Total da conversao: erro ao iniciar")
            messagebox.showerror("Erro ao iniciar", str(exc))
            return

        threading.Thread(target=self.read_process_output, daemon=True).start()
        threading.Thread(target=self.wait_process, daemon=True).start()

    def read_process_output(self):
        try:
            for line in self.process.stdout:
                self.log_queue.put(("log", line))
        except Exception as exc:
            self.log_queue.put(("log", f"[ERRO] Falha ao ler logs: {exc}\n"))

    def wait_process(self):
        code = self.process.wait()
        self.log_queue.put(("done", code))

    def process_running(self):
        return self.process is not None and self.process.poll() is None

    def cancel_process(self):
        if not self.process_running():
            return
        if not messagebox.askyesno("Cancelar", "Cancelar a conversao em andamento?"):
            return
        self.log("[INFO] Cancelando processo...\n")
        try:
            self.process.terminate()
        except Exception:
            pass
        self.after(3000, self.kill_if_running)

    def kill_if_running(self):
        if self.process_running():
            try:
                self.process.kill()
            except Exception:
                pass

    def run_test(self, mode):
        if self.process_running():
            return
        self.set_connection_status(mode, "testing")
        self.log(f"\n[TESTE] Executando {mode}...\n")
        threading.Thread(target=self.run_test_thread, args=(mode,), daemon=True).start()

    def run_test_thread(self, mode):
        try:
            completed = subprocess.run(
                worker_command(mode),
                cwd=APP_DIR,
                env=self.env_for_process(),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            self.log_queue.put(("log", completed.stdout))
            if completed.returncode == 0:
                self.log_queue.put(("test_status", (mode, "ok")))
                self.log_queue.put(("log", "[OK] Teste finalizado com sucesso.\n"))
            else:
                self.log_queue.put(("test_status", (mode, "error")))
                self.log_queue.put(("log", f"[ERRO] Teste retornou codigo {completed.returncode}.\n"))
        except Exception as exc:
            self.log_queue.put(("test_status", (mode, "error")))
            self.log_queue.put(("log", f"[ERRO] Teste falhou: {exc}\n"))

    def drain_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    marker_only = self.parse_progress(payload)
                    if not marker_only:
                        self.log(payload)
                        self.maybe_ask_final_decision(payload)
                elif kind == "test_status":
                    mode, status = payload
                    self.set_connection_status(mode, status)
                elif kind == "reverter_done":
                    if not self.process_running():
                        self.reverter_btn.configure(state="normal")
                elif kind == "done":
                    self.finish_process(payload)
        except queue.Empty:
            pass
        self.after(100, self.drain_log_queue)

    def maybe_ask_final_decision(self, text):
        if self.final_decision_sent or self.awaiting_final_decision:
            return
        if "Deseja CONFIRMAR (commit) ou CANCELAR (rollback)? [C/R]" not in text:
            return
        self.awaiting_final_decision = True
        self.total_label.set("Total da conversao: aguardando COMMIT/ROLLBACK - 100.0%")
        commit = self.ask_commit()
        resposta = "C" if commit else "R"
        try:
            if self.process and self.process.stdin:
                self.process.stdin.write(resposta + "\n")
                self.process.stdin.flush()
                self.process.stdin.close()
            self.final_decision_sent = True
            self.log(f"[GUI] Resposta enviada: {'COMMIT' if commit else 'ROLLBACK'}.\n")
        except Exception as exc:
            self.log(f"[ERRO] Nao foi possivel enviar COMMIT/ROLLBACK: {exc}\n")
        finally:
            self.awaiting_final_decision = False

    def log(self, text):
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def clear_logs(self):
        self.log_text.delete("1.0", "end")

    def init_progress(self, selected_keys=None):
        self.progress_task_keys = list(selected_keys or [])
        if not self.progress_task_keys:
            self.progress_task_keys = ["conversao"]
        self.progress_current_index = -1
        self.progress_current_key = None
        self.progress_item_value = 0
        self.progress_item_total = 0
        self.progress_marker_mode = False
        self.progress_completed_keys = set()
        self.total_mode_indeterminate = False
        self.item_bar.configure(mode="determinate", maximum=100, value=0)
        self.total_bar.configure(mode="determinate", maximum=100, value=0)
        self.update_progress_labels()

    def detect_progress_key(self, text):
        texto = text.lower()
        for key, keywords in PROGRESS_KEYWORDS:
            if key not in self.progress_task_keys:
                continue
            if any(keyword in texto for keyword in keywords):
                return key
        return None

    def set_progress_table(self, key):
        if key not in self.progress_task_keys:
            return
        idx = self.progress_task_keys.index(key)
        if idx < self.progress_current_index:
            return
        if idx != self.progress_current_index:
            if self.progress_current_key is not None:
                self.mark_progress_table_done(self.progress_current_key)
            self.progress_current_index = idx
            self.progress_current_key = key
            self.progress_item_value = 0
            self.progress_item_total = 0
            self.item_bar.configure(value=0)
            self.update_progress_labels()

    def ensure_progress_table_started(self):
        if self.progress_current_key is not None:
            return
        if self.progress_task_keys:
            self.set_progress_table(self.progress_task_keys[0])

    def mark_progress_table_done(self, key):
        if key in self.progress_task_keys:
            self.progress_completed_keys.add(key)
            self.update_progress_labels()

    def update_progress_labels(self):
        total_tarefas = len(self.progress_task_keys)
        atual = self.progress_item_value
        total = self.progress_item_total
        if total > 0:
            percentual_tabela = min(100.0, (atual / total) * 100.0)
            faltam_item = max(total - atual, 0)
            self.item_bar.configure(maximum=100, value=percentual_tabela)
            nome_tabela = PROGRESS_LABELS.get(self.progress_current_key, self.progress_current_key or "-")
            self.item_label.set(
                f"Tabela atual: {nome_tabela} - {atual}/{total} "
                f"({percentual_tabela:.1f}%, faltam {faltam_item})"
            )
        else:
            percentual_tabela = 0.0
            nome_tabela = PROGRESS_LABELS.get(self.progress_current_key, self.progress_current_key or "aguardando logs")
            self.item_bar.configure(maximum=100, value=0)
            self.item_label.set(f"Tabela atual: {nome_tabela} - 0.0%")

        concluidas = len(self.progress_completed_keys)
        if total_tarefas > 0:
            percentual_total = min(100.0, (concluidas / total_tarefas) * 100.0)
            faltam_tarefas = max(total_tarefas - concluidas, 0)
        else:
            percentual_total = 0.0
            faltam_tarefas = 0
        self.total_bar.configure(maximum=100, value=percentual_total)
        self.total_label.set(
            f"Total da conversao: {percentual_total:.1f}% "
            f"({concluidas}/{total_tarefas} tabelas concluidas, faltam {faltam_tarefas})"
        )

    def parse_progress(self, text):
        marker = re.search(r"\[GUI_PROGRESS\]\s+tabela=([a-z_]+)", text)
        if marker:
            self.progress_marker_mode = True
            self.set_progress_table(marker.group(1))
            return True

        match = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            self.ensure_progress_table_started()
            atual = int(match.group(1))
            total = int(match.group(2))
            if total > 0:
                self.progress_item_value = min(atual, total)
                self.progress_item_total = total
                self.update_progress_labels()

        if "RESUMO DA CONVERSAO" in text:
            if self.progress_current_key is not None:
                self.progress_item_value = self.progress_item_total or 1
                self.progress_item_total = self.progress_item_total or 1
                self.mark_progress_table_done(self.progress_current_key)
            percentual_total = float(self.total_bar["value"] or 0)
            self.total_label.set(
                f"Total da conversao: gerando resumo - "
                f"{percentual_total:.1f}%"
            )
        if "[OK] COMMIT realizado" in text or "[OK] ROLLBACK realizado" in text:
            self.total_bar.configure(value=100)
            self.total_label.set("Total da conversao: finalizando - 100.0%")
        return False

    def finish_process(self, code):
        try:
            if self.process and self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        except Exception:
            pass
        self.set_running(False)
        self.process = None
        if code == 0:
            self.progress_completed_keys.update(self.progress_task_keys)
            self.item_bar.configure(value=100)
            self.total_bar.configure(value=100)
            self.item_label.set("Tabela atual: concluida - 100.0%")
            self.total_label.set("Total da conversao: concluido - 100.0%")
            self.log("\n[OK] Processo concluido.\n")
        else:
            self.total_label.set("Total da conversao: interrompido/erro")
            self.log(f"\n[ERRO] Processo terminou com codigo {code}.\n")

    def set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.reverter_btn.configure(state="disabled" if running else "normal")

    def update_timer(self):
        if self.start_time and self.process_running():
            elapsed = int(time.monotonic() - self.start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.time_label.set(f"Tempo: {h:02d}:{m:02d}:{s:02d}")
        self.after(500, self.update_timer)


def main():
    app = ConverterGUI()
    app.mainloop()


if __name__ == "__main__":
    if "--run-converter" in sys.argv:
        run_converter_mode()
    elif "--test-giv" in sys.argv:
        test_giv_mode()
    elif "--test-web" in sys.argv:
        test_web_mode()
    else:
        main()
