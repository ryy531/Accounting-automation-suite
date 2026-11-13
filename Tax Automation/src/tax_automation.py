# =============================================================================
# tax_automation.py — Main Menu
# Run this file to access all Tax Automation tools
# =============================================================================

import os
import re
import sys


def clear():
    os.system("cls" if os.name == "nt" else "clear")


# ── TAX_YEAR startup check ─────────────────────────────────────────────────────

def check_tax_year():
    """
    Runs once at startup.
    Shows the current TAX_YEAR, asks if it needs updating.
    If yes, prompts for a new 4-digit year and writes it back to config.py.
    """
    import config

    current_year = config.TAX_YEAR
    config_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")

    print("\n" + "─" * 52)
    print(f"  TAX YEAR CHECK")
    print(f"  Current TAX_YEAR in config.py : {current_year}")
    print("─" * 52)
    ans = input("  Is this correct? (Y / N): ").strip().upper()

    if ans != "N":
        return  # nothing to do

    while True:
        new_year = input("  Enter new TAX_YEAR (4-digit, e.g. 2025): ").strip()
        if re.fullmatch(r"\d{4}", new_year):
            break
        print("  Invalid — please enter a 4-digit year.")

    # Write new value back into config.py
    with open(config_path, encoding="utf-8") as f:
        content = f.read()

    updated = re.sub(
        r'^(TAX_YEAR\s*=\s*")[^"]*(")',
        rf'\g<1>{new_year}\g<2>',
        content,
        flags=re.MULTILINE,
    )

    if updated == content:
        print("  [WARN] Could not locate TAX_YEAR line — please update config.py manually.")
        return

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(updated)

    # Reload so the rest of this session uses the new value
    import importlib
    importlib.reload(config)

    print(f"  TAX_YEAR updated to {new_year} and saved to config.py.")
    print("─" * 52)


MENU = """
╔══════════════════════════════════════════════════╗
║         Tax Automation — Main Menu               ║
║         ExampleCo CPA — 2025 Personal Tax          ║
╠══════════════════════════════════════════════════╣
║                                                  ║
║  ── Setup ───────────────────────────────────── ║
║  1.  Create client folders from NewClient.xlsx   ║
║                                                  ║
║  ── Intake ──────────────────────────────────── ║
║  2.  AI Tax Info Extraction                      ║
║      (extract client info → fill Info Template)  ║
║                                                  ║
║  3.  AI File Renamer   (→ date vendor amount)    ║
║                                                  ║
║  4.  Prior year client reminder                  ║
║      (find missing clients → send reminder)      ║
║                                                  ║
║  ── Client Comms (Phase 2) ──────────────────── ║
║  5.  Send client package emails                  ║
║  6.  Send follow-up reminders                    ║
║  7.  Send efile confirmation emails              ║
║  8.  Generate PayPal payment links               ║
║                                                  ║
║  ── TaxFolder ───────────────────────────────── ║
║  9.  TaxFolder — Download & Sign/Pay Tracker     ║
║                                                  ║
║  0.  Exit                                        ║
║                                                  ║
╚══════════════════════════════════════════════════╝
"""


def run_create_folders():
    print("\n  Starting: Client Folder Creation...")
    try:
        import create_folders
        create_folders.create_folders()
    except ImportError as e:
        print(f"  [ERROR] Could not load create_folders.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def run_ai_extract_info():
    print("\n  Starting: AI Tax Info Extraction...")
    try:
        import ai_extract_info
        ai_extract_info.ai_extract_info()
    except ImportError as e:
        print(f"  [ERROR] Could not load ai_extract_info.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def run_rename_cra():
    print("\n  Starting: AI File Renamer...")
    try:
        import ai_rename
        ai_rename.ai_rename()
    except ImportError as e:
        print(f"  [ERROR] Could not load ai_rename.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def run_missing_client_reminder():
    print("\n  Starting: Prior Year Client Reminder...")
    try:
        import missing_client_reminder
        missing_client_reminder.missing_client_reminder()
    except ImportError as e:
        print(f"  [ERROR] Could not load missing_client_reminder.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def run_send_package_email():
    print("\n  Starting: Send Client Package Emails...")
    try:
        import send_package_email
        send_package_email.send_package_email()
    except ImportError as e:
        print(f"  [ERROR] Could not load send_package_email.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def run_send_efile_email():
    print("\n  Starting: Efile Confirmation Emails...")
    try:
        import send_efile_email
        send_efile_email.send_efile_email()
    except ImportError as e:
        print(f"  [ERROR] Could not load send_efile_email.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


TAXFOLDER_SUBMENU = """
  ┌─────────────────────────────────────────────┐
  │  Menu 9 — TaxFolder                         │
  ├─────────────────────────────────────────────┤
  │  1.  Download CSV + refresh tracker         │
  │  2.  Refresh tracker only (use saved CSV)   │
  │  3.  Move ready clients → 7. To efile       │
  │      (Completed + Paid → auto-move folder)  │
  │  4.  Send sign & pay reminder emails        │
  │      (to clients not yet signed/paid)       │
  │  0.  Back                                   │
  └─────────────────────────────────────────────┘"""


def run_taxfolder_download():
    import config
    try:
        import taxfolder_download
    except ImportError as e:
        print(f"\n  [ERROR] Could not load taxfolder_download.py: {e}")
        return

    while True:
        print(TAXFOLDER_SUBMENU)
        sub = input("  Enter choice: ").strip()

        if sub == "1":
            print("\n  Starting: TaxFolder Download + Tracker...")
            try:
                taxfolder_download.download_taxfolder()
            except Exception as e:
                print(f"  [ERROR] {e}")
            break

        elif sub == "2":
            csv_path = config.TAXFOLDER_EXPORT
            if not os.path.exists(csv_path):
                print(f"\n  [ERROR] No saved CSV found at:\n  {csv_path}")
                print("  Please run option 1 first to download from TaxFolder.")
            else:
                print(f"\n  Using saved CSV: {csv_path}")
                try:
                    taxfolder_download.generate_sign_pay_tracker(csv_path)
                except Exception as e:
                    print(f"  [ERROR] {e}")
            break

        elif sub == "3":
            print("\n  Checking tracker for ready clients...")
            try:
                taxfolder_download.move_ready_to_efile()
            except Exception as e:
                print(f"  [ERROR] {e}")
            break

        elif sub == "4":
            print("\n  Starting: Send Sign & Pay Reminders...")
            try:
                import send_signpay_reminder
                send_signpay_reminder.send_signpay_reminder()
            except Exception as e:
                print(f"  [ERROR] {e}")
                import traceback
                traceback.print_exc()
            input("\n  按回车返回菜单...")
            break

        elif sub == "0":
            break
        else:
            print("  Invalid choice. Please enter 0, 1, 2, 3, or 4.")


def run_send_reminder_email():
    print("\n  Starting: Follow-up Reminders...")
    try:
        import send_reminder_email
        send_reminder_email.send_reminder_email()
    except ImportError as e:
        print(f"  [ERROR] Could not load send_reminder_email.py: {e}")
    except Exception as e:
        print(f"  [ERROR] {e}")


def coming_soon(name: str):
    print(f"\n  [{name}] is planned for Phase 2.")
    print("  It will be added in a future update.")


def main():
    # Ensure we run from the script's own directory so imports work
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    sys.path.insert(0, script_dir)

    clear()
    check_tax_year()

    while True:
        clear()
        print(MENU)
        choice = input("  Enter choice: ").strip()

        if choice == "1":
            run_create_folders()
        elif choice == "2":
            run_ai_extract_info()
        elif choice == "3":
            run_rename_cra()
        elif choice == "4":
            run_missing_client_reminder()
        elif choice == "5":
            run_send_package_email()
        elif choice == "6":
            run_send_reminder_email()
        elif choice == "7":
            run_send_efile_email()
        elif choice == "8":
            coming_soon("Generate PayPal payment links")
        elif choice == "9":
            run_taxfolder_download()
        elif choice == "0":
            print("\n  Goodbye.\n")
            break
        else:
            print("\n  Invalid choice. Please enter 0–8.")

        input("\n  Press Enter to return to menu...")


if __name__ == "__main__":
    main()
