from decimal import Decimal
from pathlib import Path
import unittest

from receipts.iif.account_list import Account, AccountList
from receipts.iif.iif_writer import write_receipt_iif
from receipts.iif.schema import (
    QB_FIELD_ACCOUNT,
    QB_FIELD_INCLUDE,
    QB_FIELD_MEMO,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_PAYEE,
    QB_FIELD_REF_NO,
    QB_FIELD_TRANSACTION_TYPE,
    WorkbookRow,
)
from receipts.iif.vendor_list import Vendor, VendorList


def _accounts(*names: str) -> AccountList:
    return AccountList(
        accounts={
            name: Account(name=name, account_type="", row={"NAME": name})
            for name in names
        }
    )


def _vendors(*names: str) -> VendorList:
    return VendorList(
        vendors={
            name: Vendor(name=name, active_status="Active", row={"NAME": name})
            for name in names
        }
    )


def _typed_accounts(types: dict[str, str]) -> AccountList:
    return AccountList(
        accounts={
            name: Account(name=name, account_type=account_type, row={"NAME": name})
            for name, account_type in types.items()
        }
    )


def _row(
    *,
    row_number: int,
    transaction_type: str,
    payee: str,
    account: str,
    payment_account: str,
    total: str,
    ref_no: str,
    memo: str,
    gst: str = "0",
    pst: str = "0",
) -> WorkbookRow:
    return WorkbookRow(
        sheet_name="Receipts",
        row_number=row_number,
        values={
            QB_FIELD_INCLUDE: True,
            QB_FIELD_TRANSACTION_TYPE: transaction_type,
            QB_FIELD_PAYEE: payee,
            QB_FIELD_ACCOUNT: account,
            QB_FIELD_PAYMENT_ACCOUNT: payment_account,
            QB_FIELD_MEMO: memo,
            QB_FIELD_REF_NO: ref_no,
            "date": "5/3/2026",
            "description": memo,
            "total": total,
            "gst": gst,
            "pst": pst,
            "needs_review": "False",
        },
    )


def _read_records(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="cp1252").splitlines()
    header = lines[0].split("\t")
    assert header[0] == "!TRNS"
    fields = header[1:]
    records: list[dict[str, str]] = []

    for line in lines:
        columns = line.split("\t")
        if columns[0] not in {"TRNS", "SPL"}:
            continue
        row = {"_record": columns[0]}
        row.update(dict(zip(fields, columns[1:])))
        records.append(row)

    return header, records


class IifWriterCanadaFormatTests(unittest.TestCase):
    def test_bank_paid_receipt_uses_confirmed_canada_cheque_format(self):
        output_path = Path(self.tmpdir) / "cheque.iif"
        row = _row(
            row_number=2,
            transaction_type="Cheque",
            payee="SaskPower Test",
            account="Utilities",
            payment_account="Test BMO Chequing",
            total="112.00",
            gst="5.00",
            pst="7.00",
            ref_no="CHK-1002",
            memo="Utilities tax split test",
        )

        result = write_receipt_iif(
            [row],
            _accounts(
                "Test BMO Chequing",
                "Utilities",
                "GST/HST Payable",
                "PST Payable (SK)",
            ),
            output_path,
        )

        self.assertEqual(result.exported_rows, 1)
        header, records = _read_records(output_path)
        self.assertEqual(
            header,
            ["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO"],
        )

        trns = records[0]
        spls = records[1:]
        self.assertEqual(trns["_record"], "TRNS")
        self.assertEqual(trns["TRNSTYPE"], "CHEQUE")
        self.assertNotEqual(trns["TRNSTYPE"], "CHECK")
        self.assertEqual(trns["NAME"], "SaskPower Test")
        self.assertEqual(Decimal(trns["AMOUNT"]), Decimal("-112.00"))

        self.assertEqual([split["ACCNT"] for split in spls], ["Utilities", "GST/HST Payable", "PST Payable (SK)"])
        self.assertTrue(all(split["NAME"] == "" for split in spls))
        self.assertTrue(all(Decimal(split["AMOUNT"]) > 0 for split in spls))
        self.assertEqual(sum(Decimal(record["AMOUNT"]) for record in records), Decimal("0.00"))

    def test_credit_card_receipt_uses_confirmed_canada_charge_format(self):
        output_path = Path(self.tmpdir) / "credit_card.iif"
        row = _row(
            row_number=3,
            transaction_type="Credit Card Charge",
            payee="Costco Test",
            account="Supplies",
            payment_account="Test Visa",
            total="84.00",
            ref_no="VISA-2002",
            memo="Supplies credit card test",
        )

        result = write_receipt_iif(
            [row],
            _accounts("Test Visa", "Supplies", "GST/HST Payable", "PST Payable (SK)"),
            output_path,
        )

        self.assertEqual(result.exported_rows, 1)
        header, records = _read_records(output_path)
        self.assertEqual(
            header,
            ["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO"],
        )

        trns = records[0]
        spls = records[1:]
        self.assertEqual(trns["TRNSTYPE"], "CREDIT CARD")
        self.assertEqual(trns["NAME"], "Costco Test")
        self.assertEqual(Decimal(trns["AMOUNT"]), Decimal("-84.00"))
        self.assertTrue(all(split["NAME"] == "" for split in spls))
        self.assertTrue(all(Decimal(split["AMOUNT"]) > 0 for split in spls))
        self.assertEqual(sum(Decimal(record["AMOUNT"]) for record in records), Decimal("0.00"))

    def test_reviewed_new_vendor_is_written_before_transaction_headers_once(self):
        output_path = Path(self.tmpdir) / "new_vendor.iif"
        rows = [
            _row(
                row_number=4,
                transaction_type="Credit Card Charge",
                payee="New Vendor Test",
                account="Supplies",
                payment_account="Test Visa",
                total="10.00",
                ref_no="VISA-3001",
                memo="First new vendor test",
            ),
            _row(
                row_number=5,
                transaction_type="Credit Card Charge",
                payee="New Vendor Test",
                account="Supplies",
                payment_account="Test Visa",
                total="20.00",
                ref_no="VISA-3002",
                memo="Duplicate new vendor test",
            ),
            _row(
                row_number=6,
                transaction_type="Credit Card Charge",
                payee="Costco Test",
                account="Supplies",
                payment_account="Test Visa",
                total="30.00",
                ref_no="VISA-3003",
                memo="Existing vendor test",
            ),
        ]

        result = write_receipt_iif(
            rows,
            _accounts("Test Visa", "Supplies", "GST/HST Payable", "PST Payable (SK)"),
            output_path,
            vendors=_vendors("Costco Test"),
            allow_vendor_creation=True,
        )

        lines = output_path.read_text(encoding="cp1252").splitlines()
        self.assertEqual(result.exported_rows, 3)
        self.assertEqual(result.new_vendors, ["New Vendor Test"])
        self.assertEqual(lines[:2], ["!VEND\tNAME", "VEND\tNew Vendor Test"])
        self.assertEqual(lines[2].split("\t")[0], "!TRNS")
        self.assertEqual(lines.count("VEND\tNew Vendor Test"), 1)
        self.assertNotIn("VEND\tCostco Test", lines)

    def test_description_is_vendor_and_needs_review_does_not_block_export(self):
        output_path = Path(self.tmpdir) / "description_vendor.iif"
        row = WorkbookRow(
            sheet_name="Receipts",
            row_number=7,
            values={
                QB_FIELD_INCLUDE: True,
                QB_FIELD_TRANSACTION_TYPE: "Credit Card Charge",
                QB_FIELD_ACCOUNT: "Supplies",
                QB_FIELD_PAYMENT_ACCOUNT: "Test Visa",
                QB_FIELD_MEMO: "Reviewed description vendor test",
                QB_FIELD_REF_NO: "VISA-4001",
                "date": "5/3/2026",
                "description": "The Centre Co-op",
                "total": "42.00",
                "gst": "0",
                "pst": "0",
                "needs_review": "True",
            },
        )

        result = write_receipt_iif(
            [row],
            _accounts("Test Visa", "Supplies", "GST/HST Payable", "PST Payable (SK)"),
            output_path,
            vendors=_vendors("Costco Test"),
            allow_vendor_creation=True,
        )

        lines = output_path.read_text(encoding="cp1252").splitlines()
        trns_header_index = next(index for index, line in enumerate(lines) if line.startswith("!TRNS\t"))
        header = lines[trns_header_index].split("\t")
        fields = header[1:]
        trns_columns = lines[trns_header_index + 3].split("\t")
        trns = {"_record": trns_columns[0]}
        trns.update(dict(zip(fields, trns_columns[1:])))

        self.assertEqual(result.exported_rows, 1)
        self.assertEqual(result.blocked_rows, 0)
        self.assertEqual(result.new_vendors, ["The Centre Co-op"])
        self.assertEqual(header[0], "!TRNS")
        self.assertEqual(trns["NAME"], "The Centre Co-op")

    def test_credit_card_charge_blocks_bank_payment_account(self):
        output_path = Path(self.tmpdir) / "bad_payment_account.iif"
        row = _row(
            row_number=8,
            transaction_type="Credit Card Charge",
            payee="Costco Test",
            account="Supplies",
            payment_account="Test BMO Chequing",
            total="107.85",
            ref_no="VISA-5001",
            memo="Credit card type mismatch test",
        )

        result = write_receipt_iif(
            [row],
            _typed_accounts(
                {
                    "Test BMO Chequing": "Bank",
                    "Supplies": "Expense",
                    "GST/HST Payable": "Other Current Liability",
                    "PST Payable (SK)": "Other Current Liability",
                }
            ),
            output_path,
            vendors=_vendors("Costco Test"),
        )

        self.assertEqual(result.exported_rows, 0)
        self.assertEqual(result.blocked_rows, 1)
        self.assertIn("Credit Card Charge requires a Credit Card type Payment Account", result.blocks[0].reason)

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
