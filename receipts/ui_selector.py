from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from openpyxl import load_workbook

from .iif.account_list import load_accounts


ACCOUNT_FILE_TYPES = (
    ("QuickBooks account list", "*.xlsx *.xlsm *.iif"),
    ("Excel files", "*.xlsx *.xlsm"),
    ("IIF files", "*.iif"),
    ("All files", "*.*"),
)

EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
ACCOUNT_LIST_SHEET_NAME = "account_list"
VENDOR_LIST_SHEET_NAME = "vendor_list"


def _center(root: tk.Tk) -> None:
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"+{x}+{y}")


def _clean_path(value: str) -> str:
    return value.strip().strip('"')


def _bank_payment_account_names(account_path: str) -> list[str]:
    if not account_path:
        return []
    path = Path(account_path)
    if not path.exists():
        return []
    try:
        return load_accounts(path).bank_payment_account_names()
    except Exception:
        return []


def _browse_account_list(account_var: tk.StringVar, on_loaded=None) -> None:
    path = filedialog.askopenfilename(
        title="Select this company's QuickBooks account list",
        filetypes=ACCOUNT_FILE_TYPES,
    )
    if path:
        account_var.set(path)
        if on_loaded:
            on_loaded(path)


def _browse_vendor_list(vendor_var: tk.StringVar) -> None:
    path = filedialog.askopenfilename(
        title="Select this company's QuickBooks vendor list",
        filetypes=ACCOUNT_FILE_TYPES,
    )
    if path:
        vendor_var.set(path)


def _validate_account_path(account_path: str) -> bool:
    if not account_path:
        messagebox.showerror(
            "Missing account list",
            "Please provide this company's QuickBooks account list before processing.",
        )
        return False

    path = Path(account_path)
    if not path.exists():
        messagebox.showerror("Invalid account list", f"Account list file not found:\n{account_path}")
        return False

    if path.suffix.lower() not in {".xlsx", ".xlsm", ".iif"}:
        messagebox.showerror(
            "Invalid account list",
            "Account list must be an .xlsx, .xlsm, or .iif file.",
        )
        return False

    return True


def _validate_vendor_path(vendor_path: str) -> bool:
    if not vendor_path:
        messagebox.showerror(
            "Missing vendor list",
            "Please provide this company's QuickBooks vendor list before processing.",
        )
        return False

    path = Path(vendor_path)
    if not path.exists():
        messagebox.showerror("Invalid vendor list", f"Vendor list file not found:\n{vendor_path}")
        return False

    if path.suffix.lower() not in {".xlsx", ".xlsm", ".iif"}:
        messagebox.showerror(
            "Invalid vendor list",
            "Vendor list must be an .xlsx, .xlsm, or .iif file.",
        )
        return False

    return True


def _has_embedded_account_list(input_path: Path) -> bool:
    try:
        workbook = load_workbook(input_path, read_only=True, data_only=True)
        try:
            return any(
                sheet_name.strip().lower() == ACCOUNT_LIST_SHEET_NAME
                for sheet_name in workbook.sheetnames
            )
        finally:
            workbook.close()
    except Exception:
        return False


def _has_embedded_vendor_list(input_path: Path) -> bool:
    try:
        workbook = load_workbook(input_path, read_only=True, data_only=True)
        try:
            return any(
                sheet_name.strip().lower() == VENDOR_LIST_SHEET_NAME
                for sheet_name in workbook.sheetnames
            )
        finally:
            workbook.close()
    except Exception:
        return False


def select_run_options(input_path: Path) -> dict | None:
    if input_path.suffix.lower() in EXCEL_EXTENSIONS:
        return _select_iif_generate_options(input_path)
    return _select_ocr_options()


def select_document_type() -> str | None:
    options = select_run_options(Path(""))
    if not options:
        return None
    return options.get("declared_type")


def _select_ocr_options() -> dict | None:
    result = {"value": None}

    root = tk.Tk()
    root.title("Receipt Tool")
    root.resizable(False, False)

    selected = tk.StringVar(value="")
    qb_prefill = tk.BooleanVar(value=False)
    account_var = tk.StringVar(value="")
    vendor_var = tk.StringVar(value="")
    payment_account_var = tk.StringVar(value="")

    frame = ttk.Frame(root, padding=18)
    frame.grid(row=0, column=0, sticky="nsew")

    label = ttk.Label(frame, text="What type of documents are in this run?")
    label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

    doc_options = (
        ("receipts", "All Receipts / Invoices / Bills"),
        ("bank", "All Bank Statements"),
        ("mixed", "Mixed"),
    )

    start_button = ttk.Button(frame, text="Start", state="disabled")
    account_label = ttk.Label(frame, text="QuickBooks account list:")
    account_entry = ttk.Entry(frame, textvariable=account_var, width=54)

    def _on_account_loaded(path: str) -> None:
        names = _bank_payment_account_names(path)
        payment_combo.configure(values=names)
        if payment_account_var.get() not in names:
            payment_account_var.set(names[0] if names else "")

    browse_button = ttk.Button(
        frame,
        text="Browse...",
        command=lambda: _browse_account_list(account_var, _on_account_loaded),
    )
    vendor_label = ttk.Label(frame, text="QuickBooks vendor list:")
    vendor_entry = ttk.Entry(frame, textvariable=vendor_var, width=54)
    vendor_browse_button = ttk.Button(
        frame,
        text="Browse...",
        command=lambda: _browse_vendor_list(vendor_var),
    )
    payment_account_label = ttk.Label(frame, text="Default payment account (Bank) for these receipts:")
    payment_combo = ttk.Combobox(frame, textvariable=payment_account_var, width=51, state="readonly")

    def update_controls() -> None:
        doc_type = selected.get()
        if doc_type == "receipts":
            qb_check.configure(state="normal")
        else:
            qb_prefill.set(False)
            qb_check.configure(state="disabled")

        if qb_prefill.get():
            account_label.grid(row=5, column=0, sticky="w", pady=(12, 0))
            account_entry.grid(row=6, column=0, columnspan=2, sticky="we", pady=(4, 0))
            browse_button.grid(row=6, column=2, sticky="w", padx=(8, 0), pady=(4, 0))
            vendor_label.grid(row=7, column=0, sticky="w", pady=(12, 0))
            vendor_entry.grid(row=8, column=0, columnspan=2, sticky="we", pady=(4, 0))
            vendor_browse_button.grid(row=8, column=2, sticky="w", padx=(8, 0), pady=(4, 0))
            payment_account_label.grid(row=9, column=0, columnspan=3, sticky="w", pady=(12, 0))
            payment_combo.grid(row=10, column=0, columnspan=2, sticky="we", pady=(4, 0))
        else:
            account_label.grid_remove()
            account_entry.grid_remove()
            browse_button.grid_remove()
            vendor_label.grid_remove()
            vendor_entry.grid_remove()
            vendor_browse_button.grid_remove()
            payment_account_label.grid_remove()
            payment_combo.grid_remove()

        start_button.configure(state="normal" if doc_type else "disabled")

    for idx, (value, text) in enumerate(doc_options, start=1):
        button = ttk.Radiobutton(
            frame,
            text=text,
            value=value,
            variable=selected,
            command=update_controls,
        )
        button.grid(row=idx, column=0, columnspan=3, sticky="w", pady=4)

    qb_check = ttk.Checkbutton(
        frame,
        text="Prepare QuickBooks import after OCR",
        variable=qb_prefill,
        command=update_controls,
        state="disabled",
    )
    qb_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def start() -> None:
        doc_type = selected.get() or None
        if not doc_type:
            return

        account_path = _clean_path(account_var.get())
        if qb_prefill.get() and not _validate_account_path(account_path):
            return
        vendor_path = _clean_path(vendor_var.get())
        if qb_prefill.get() and not _validate_vendor_path(vendor_path):
            return
        payment_account = payment_account_var.get().strip()
        if qb_prefill.get() and not payment_account:
            messagebox.showerror(
                "Missing payment account",
                "Please select the default payment (Bank) account these receipts were paid from.\n"
                "Without it, every row will be left blank and flagged for review.",
            )
            return

        result["value"] = {
            "mode": "ocr",
            "declared_type": doc_type,
            "qb_prefill": bool(qb_prefill.get()),
            "account_list_path": account_path if qb_prefill.get() else "",
            "vendor_list_path": vendor_path if qb_prefill.get() else "",
            "default_payment_account": payment_account if qb_prefill.get() else "",
        }
        root.destroy()

    def cancel() -> None:
        result["value"] = None
        root.destroy()

    start_button.configure(command=start)
    start_button.grid(row=11, column=0, sticky="e", pady=(16, 0), padx=(0, 8))

    cancel_button = ttk.Button(frame, text="Cancel", command=cancel)
    cancel_button.grid(row=11, column=1, sticky="w", pady=(16, 0))

    root.protocol("WM_DELETE_WINDOW", cancel)
    update_controls()
    _center(root)
    root.mainloop()
    return result["value"]


def _select_iif_generate_options(input_path: Path) -> dict | None:
    result = {"value": None}

    root = tk.Tk()
    root.title("Generate QuickBooks IIF")
    root.resizable(False, False)

    account_var = tk.StringVar(value="")
    vendor_var = tk.StringVar(value="")
    has_embedded_accounts = _has_embedded_account_list(input_path)
    has_embedded_vendors = _has_embedded_vendor_list(input_path)

    frame = ttk.Frame(root, padding=18)
    frame.grid(row=0, column=0, sticky="nsew")

    label = ttk.Label(
        frame,
        text="Generate QuickBooks IIF from reviewed Excel workbook",
    )
    label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

    workbook_label = ttk.Label(frame, text=f"Workbook: {input_path.name}")
    workbook_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))

    if has_embedded_accounts:
        account_text = "Account list: embedded Account_List sheet found. Browse only to override."
    else:
        account_text = "This company's QuickBooks account list:"

    account_label = ttk.Label(frame, text=account_text)
    account_label.grid(row=2, column=0, columnspan=3, sticky="w")

    account_entry = ttk.Entry(frame, textvariable=account_var, width=54)
    account_entry.grid(row=3, column=0, columnspan=2, sticky="we", pady=(4, 0))

    browse_button = ttk.Button(
        frame,
        text="Browse...",
        command=lambda: _browse_account_list(account_var),
    )
    browse_button.grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(4, 0))

    if has_embedded_vendors:
        vendor_text = "Vendor list: embedded Vendor_List sheet found. Browse only to override."
    else:
        vendor_text = "This company's QuickBooks vendor list:"

    vendor_label = ttk.Label(frame, text=vendor_text)
    vendor_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

    vendor_entry = ttk.Entry(frame, textvariable=vendor_var, width=54)
    vendor_entry.grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 0))

    vendor_browse_button = ttk.Button(
        frame,
        text="Browse...",
        command=lambda: _browse_vendor_list(vendor_var),
    )
    vendor_browse_button.grid(row=5, column=2, sticky="w", padx=(8, 0), pady=(4, 0))

    def generate() -> None:
        account_path = _clean_path(account_var.get())
        if account_path and not _validate_account_path(account_path):
            return
        if not account_path and not has_embedded_accounts:
            messagebox.showerror(
                "Missing account list",
                "Please provide this company's QuickBooks account list before processing.",
            )
            return
        vendor_path = _clean_path(vendor_var.get())
        if vendor_path and not _validate_vendor_path(vendor_path):
            return
        if not vendor_path and not has_embedded_vendors:
            messagebox.showerror(
                "Missing vendor list",
                "Please provide this company's QuickBooks vendor list before processing.",
            )
            return

        result["value"] = {
            "mode": "generate_iif",
            "declared_type": "receipts",
            "account_list_path": account_path,
            "vendor_list_path": vendor_path,
        }
        root.destroy()

    def cancel() -> None:
        result["value"] = None
        root.destroy()

    generate_button = ttk.Button(frame, text="Generate IIF", command=generate)
    generate_button.grid(row=6, column=0, sticky="e", pady=(16, 0), padx=(0, 8))

    cancel_button = ttk.Button(frame, text="Cancel", command=cancel)
    cancel_button.grid(row=6, column=1, sticky="w", pady=(16, 0))

    root.protocol("WM_DELETE_WINDOW", cancel)
    _center(root)
    root.mainloop()
    return result["value"]
