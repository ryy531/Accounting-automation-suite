"""
Generate CHN and VIE T2 reminder templates from the existing ENG templates.
Copies formatting while replacing text with translations.
"""
import os
from docx import Document

TPL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ReminderTemplet")

# ─── Translations: ENG full paragraph → CHN / VIE ──────────────
CHN = {
    "Subject: T2 Filing Reminder – {MONTH} – {COMPANY}":
        "Subject: T2报税提醒 – {MONTH} – {COMPANY}",
    "If you have already submitted your documents, please ignore this email.":
        "如您已提交文件，请忽略此邮件。",
    "Dear {NAME}:": "尊敬的 {NAME}：",
    "We hope this message finds you well. With your corporation’s fiscal year end of {MONTH} now passed, we are writing to remind you that your T2 Corporate Income Tax Return will be due within six months of that date.":
        "希望您一切顺利。您的公司财年截止日为 {MONTH}，现已过去，特此提醒您的 T2 企业所得税申报将在截止日后六个月内到期。",
    "To ensure accurate and timely preparation of your return, we kindly ask you to begin gathering the required documentation. Please refer to the section below that applies to your situation:":
        "为确保税务申报的准确性和及时性，请您尽早准备所需文件。请参考以下适用于您情况的部分：",
    "Please provide the documents relevant to your client category as outlined below.":
        "请根据您的客户类别提供相关文件。",
    "If this is your corporation’s first-ever T2 filing:":
        "如果这是您公司的首次 T2 申报：",
    "Bookkeeping records (one of the following):":
        "记账记录（以下任选一项）：",
    "QuickBooks Desktop – please send us a backup copy of your company file.":
        "QuickBooks Desktop – 请发送公司文件的备份副本。",
    "QuickBooks Online – please share access with infosk@example-firm.com.":
        "QuickBooks Online – 请与 infosk@example-firm.com 共享访问权限。",
    "If you require bookkeeping assistance – please provide all business income and expense records, including invoices, receipts, and bank/credit card statements.":
        "如需记账协助 – 请提供所有业务收入和支出记录，包括发票、收据和银行/信用卡对账单。",
    "Corporate registration & CRA information:":
        "公司注册及 CRA 信息：",
    "Corporate profile report (showing director and shareholder information)":
        "公司资料报告（包含董事和股东信息）",
    "CRA Business Number (BN)":
        "CRA 商业号（BN）",
    "PST number (if applicable)":
        "PST 号码（如适用）",
    "CRA authorization – please add Example CPA Firm P.C. Inc. (BN: 123456789) as your representative via CRA My Business Account.":
        "CRA 授权 – 请通过 CRA My Business Account 添加 Example CPA Firm P.C. Inc. (BN: 123456789) 为您的代表。",
    "Additional information required from all clients (see below):":
        "所有客户需提供的额外信息（见下方）：",
    "Month-end bank, credit card, and loan statements with reconciliations":
        "月末银行、信用卡及贷款对账单及对账记录",
    "Records of capital expenditures (date of purchase and cost)":
        "资本支出记录（购买日期和成本）",
    "Details of any changes to business structure or ownership":
        "业务结构或所有权变更的详情",
    "Shareholder information: SIN (individuals) or BN (corporations), and number of shares held":
        "股东信息：SIN（个人）或 BN（公司），以及持股数量",
    "Primary contact name, phone number, and email address":
        "主要联系人姓名、电话号码和电子邮件地址",
    "If you have filed T2 returns before but are new to our firm:":
        "如果您之前申报过 T2，但是我们公司的新客户：",
    "Prior year-end package (from your previous accountant):":
        "前一年年终套件（来自您的前任会计师）：",
    "Trial balance": "试算平衡表",
    "Adjusting journal entries": "调整分录",
    "Financial statements": "财务报表",
    "T2 return": "T2 申报表",
    "If you are an existing client of our firm:":
        "如果您是我们的现有客户：",
    "QuickBooks Desktop – please send us an updated backup copy of your company file.":
        "QuickBooks Desktop – 请发送更新后的公司文件备份副本。",
    "QuickBooks Online – please confirm that our access (infosk@example-firm.com) is still active.":
        "QuickBooks Online – 请确认我们的访问权限（infosk@example-firm.com）仍然有效。",
    "Additional information:": "额外信息：",
    "Please submit your documents at your earliest convenience so we have sufficient time to review your records ahead of the filing deadline. Should you have any questions, please do not hesitate to contact our office.":
        "请尽早提交您的文件，以便我们有足够的时间在申报截止日前审查您的记录。如有任何问题，请随时联系我们的办公室。",
    "We look forward to assisting you.":
        "我们期待为您提供服务。",
    # R2
    "This is a follow-up to our previous reminder regarding the T2 Corporate Income Tax Return for {COMPANY}, for the fiscal year ending {MONTH}. As the filing deadline is approaching, we kindly ask that you submit the required documentation as soon as possible to avoid any delays or potential penalties.":
        "这是关于 {COMPANY} 截止 {MONTH} 财年的 T2 企业所得税申报的跟进提醒。申报截止日即将到来，请您尽快提交所需文件，以避免延误或潜在罚款。",
    "If you have not yet provided your records, please refer to the applicable section below:":
        "如果您尚未提供记录，请参考以下适用部分：",
    "Timely submission of your records allows us to complete your return accurately and well before the deadline. Please reach out if you need any assistance in gathering the required items.":
        "及时提交您的记录能让我们在截止日前准确完成申报。如需协助准备所需文件，请随时联系我们。",
    "We value your continued trust in our firm and look forward to hearing from you.":
        "感谢您对我们公司的持续信任，期待您的回复。",
    # R3
    "This is our third and final reminder regarding the T2 Corporate Income Tax Return for {COMPANY}, for the fiscal year ending {MONTH}. We are concerned that we have not yet received the necessary documentation and wish to draw your attention to the potential consequences of a missed filing deadline.":
        "这是关于 {COMPANY} 截止 {MONTH} 财年的 T2 企业所得税申报的第三次也是最后一次提醒。我们尚未收到必要的文件，希望引起您对错过申报截止日可能后果的重视。",
    "Please be aware of the following:":
        "请注意以下事项：",
    "Late filing of a T2 return may result in CRA penalties calculated as a percentage of any balance owing.":
        "晚申报 T2 可能导致 CRA 罚款，罚款金额按欠税额的百分比计算。",
    "Interest on outstanding balances accrues daily.":
        "欠税余额的利息每天累积。",
    "Failure to file may affect your corporation’s standing with the CRA and provincial authorities.":
        "未申报可能影响您公司在 CRA 和省级机构的信誉。",
    "We strongly urge you to contact our office immediately so we can prioritize your file. If you have not yet submitted your documentation, please refer to the applicable section below:":
        "我们强烈建议您立即联系我们的办公室，以便我们优先处理您的文件。如果您尚未提交文件，请参考以下适用部分：",
    "Please treat this matter with urgency. Our team is ready to assist you and will prioritize your file upon receipt of the required documents.":
        "请紧急处理此事。我们的团队随时准备协助您，并将在收到所需文件后优先处理您的文件。",
    "If you have already arranged filing through another firm, please advise us so we may update our records accordingly.":
        "如果您已通过其他公司安排申报，请告知我们，以便我们更新记录。",
    "This communication is intended solely for the use of the named recipient. It may contain confidential or privileged information. If you have received this message in error, please notify us immediately and do not copy, distribute, or act upon its contents.":
        "本通信仅供指定收件人使用。它可能包含机密或特权信息。如果您错误地收到了此消息，请立即通知我们，不要复制、分发或根据其内容采取行动。",
}

VIE = {
    "Subject: T2 Filing Reminder – {MONTH} – {COMPANY}":
        "Subject: Nhắc nhở nộp T2 – {MONTH} – {COMPANY}",
    "If you have already submitted your documents, please ignore this email.":
        "Nếu bạn đã gửi tài liệu, vui lòng bỏ qua email này.",
    "Dear {NAME}:": "Kính gửi {NAME},",
    "We hope this message finds you well. With your corporation’s fiscal year end of {MONTH} now passed, we are writing to remind you that your T2 Corporate Income Tax Return will be due within six months of that date.":
        "Chúng tôi hy vọng bạn khỏee mạnh. Năm tài chính của công ty bạn kết thúc vào {MONTH} đã qua, chúng tôi viết để nhắc nhở rằng Bản khai thuế thu nhập doanh nghiệp T2 sẽ đến hạn trong sáu tháng kể từ ngày đó.",
    "To ensure accurate and timely preparation of your return, we kindly ask you to begin gathering the required documentation. Please refer to the section below that applies to your situation:":
        "Để đảm bảo chuẩn bị chính xác và kịp thời, chúng tôi nhờ bạn bắt đầu thu thập các tài liệu cần thiết. Vui lòng tham khảo phần dưới đây áp dụng cho trường hợp của bạn:",
    "Please provide the documents relevant to your client category as outlined below.":
        "Vui lòng cung cấp các tài liệu liên quan đến loại khách hàng của bạn như dưới đây.",
    "If this is your corporation’s first-ever T2 filing:":
        "Nếu đây là lần đầu tiên công ty của bạn khai T2:",
    "Bookkeeping records (one of the following):":
        "Hồ sơ kế toán (một trong các loại sau):",
    "QuickBooks Desktop – please send us a backup copy of your company file.":
        "QuickBooks Desktop – vui lòng gửi cho chúng tôi bản sao lưu của tệp công ty.",
    "QuickBooks Online – please share access with infosk@example-firm.com.":
        "QuickBooks Online – vui lòng chia sẻ quyền truy cập với infosk@example-firm.com.",
    "If you require bookkeeping assistance – please provide all business income and expense records, including invoices, receipts, and bank/credit card statements.":
        "Nếu cần hỗ trợ kế toán – vui lòng cung cấp tất cả hồ sơ thu nhập và chi phí, bao gồm hóa đơn, biên lai và sao kê ngân hàng/thẻ tín dụng.",
    "Corporate registration & CRA information:":
        "Đăng ký công ty và thông tin CRA:",
    "Corporate profile report (showing director and shareholder information)":
        "Báo cáo hồ sơ công ty (thể hiện thông tin giám đốc và cổ đông)",
    "CRA Business Number (BN)":
        "Số doanh nghiệp CRA (BN)",
    "PST number (if applicable)":
        "Số PST (nếu có)",
    "CRA authorization – please add Example CPA Firm P.C. Inc. (BN: 123456789) as your representative via CRA My Business Account.":
        "Ủy quyền CRA – vui lòng thêm Example CPA Firm P.C. Inc. (BN: 123456789) làm đại diện qua CRA My Business Account.",
    "Additional information required from all clients (see below):":
        "Thông tin bổ sung yêu cầu từ tất cả khách hàng (xem dưới đây):",
    "Month-end bank, credit card, and loan statements with reconciliations":
        "Sao kê ngân hàng, thẻ tín dụng và khoản vay cuối tháng kèm đối chiếu",
    "Records of capital expenditures (date of purchase and cost)":
        "Hồ sơ chi tiêu vốn (ngày mua và chi phí)",
    "Details of any changes to business structure or ownership":
        "Chi tiết về mọi thay đổi về cấu trúc doanh nghiệp hoặc sở hữu",
    "Shareholder information: SIN (individuals) or BN (corporations), and number of shares held":
        "Thông tin cổ đông: SIN (cá nhân) hoặc BN (công ty) và sổ cổ phần nắm giữ",
    "Primary contact name, phone number, and email address":
        "Tên liên hệ chính, số điện thoại và địa chỉ email",
    "If you have filed T2 returns before but are new to our firm:":
        "Nếu bạn đã từng khai T2 nhưng là khách hàng mới của chúng tôi:",
    "Prior year-end package (from your previous accountant):":
        "Hồ sơ cuối năm trước (từ kế toán trước đây):",
    "Trial balance": "Bảng cân đối phát sinh",
    "Adjusting journal entries": "Bút toán điều chỉnh",
    "Financial statements": "Báo cáo tài chính",
    "T2 return": "Tờ khai T2",
    "If you are an existing client of our firm:":
        "Nếu bạn là khách hàng hiện tại của chúng tôi:",
    "QuickBooks Desktop – please send us an updated backup copy of your company file.":
        "QuickBooks Desktop – vui lòng gửi bản sao lưu đã cập nhật của tệp công ty.",
    "QuickBooks Online – please confirm that our access (infosk@example-firm.com) is still active.":
        "QuickBooks Online – vui lòng xác nhận quyền truy cập của chúng tôi (infosk@example-firm.com) vẫn còn hiệu lực.",
    "Additional information:": "Thông tin bổ sung:",
    "Please submit your documents at your earliest convenience so we have sufficient time to review your records ahead of the filing deadline. Should you have any questions, please do not hesitate to contact our office.":
        "Vui lòng gửi tài liệu sớm nhất có thể để chúng tôi có đủ thời gian xem xét hồ sơ của bạn trước hạn chót. Nếu bạn có bất kỳ câu hỏi nào, vui lòng liên hệ văn phòng của chúng tôi.",
    "We look forward to assisting you.":
        "Chúng tôi mong được hỗ trợ bạn.",
    # R2
    "This is a follow-up to our previous reminder regarding the T2 Corporate Income Tax Return for {COMPANY}, for the fiscal year ending {MONTH}. As the filing deadline is approaching, we kindly ask that you submit the required documentation as soon as possible to avoid any delays or potential penalties.":
        "Đây là nhắc nhở tiếp theo về Bản khai thuế T2 cho {COMPANY}, năm tài chính kết thúc {MONTH}. Hạn chót đang gần đến, vui lòng gửi tài liệu cần thiết sớm nhất có thể để trách trì hoãn hoặc phạt.",
    "If you have not yet provided your records, please refer to the applicable section below:":
        "Nếu bạn chưa cung cấp hồ sơ, vui lòng tham khảo phần áp dụng dưới đây:",
    "Timely submission of your records allows us to complete your return accurately and well before the deadline. Please reach out if you need any assistance in gathering the required items.":
        "Gửi hồ sơ đúng hạn giúp chúng tôi hoàn thành khai thuế chính xác và trước hạn. Vui lòng liên hệ nếu cần hỗ trợ thu thập các mục cần thiết.",
    "We value your continued trust in our firm and look forward to hearing from you.":
        "Chúng tôi trân trọng sự tin tưởng liên tục của bạn và mong đợi ý kiến từ bạn.",
    # R3
    "This is our third and final reminder regarding the T2 Corporate Income Tax Return for {COMPANY}, for the fiscal year ending {MONTH}. We are concerned that we have not yet received the necessary documentation and wish to draw your attention to the potential consequences of a missed filing deadline.":
        "Đây là nhắc nhở lần thứ ba và cuối cùng về Bản khai thuế T2 cho {COMPANY}, năm tài chính kết thúc {MONTH}. Chúng tôi lo ngại vì chưa nhận được tài liệu cần thiết và muốn nhắc bạn về hậu quả của việc trì hoãn khai thuế.",
    "Please be aware of the following:":
        "Vui lòng lưu ý những điềm sau:",
    "Late filing of a T2 return may result in CRA penalties calculated as a percentage of any balance owing.":
        "Khai T2 trễ có thể dẫn đến hình phạt CRA được tính theo phần trăm số dư nợ.",
    "Interest on outstanding balances accrues daily.":
        "Lãi trên số dư chưa thanh toán tích lũy hàng ngày.",
    "Failure to file may affect your corporation’s standing with the CRA and provincial authorities.":
        "Không khai thuế có thể ảnh hưởng đến tư cách của công ty với CRA và cơ quan tỉnh.",
    "We strongly urge you to contact our office immediately so we can prioritize your file. If you have not yet submitted your documentation, please refer to the applicable section below:":
        "Chúng tôi kính mời bạn liên hệ văn phòng ngay lập tức để chúng tôi ưu tiên hồ sơ của bạn. Nếu chưa gửi tài liệu, vui lòng tham khảo phần áp dụng dưới đây:",
    "Please treat this matter with urgency. Our team is ready to assist you and will prioritize your file upon receipt of the required documents.":
        "Vui lòng xử lý vấn đề này khẩn cấp. Đội ngũ của chúng tôi sẵn sàng hỗ trợ và sẽ ưu tiên hồ sơ của bạn khi nhận được tài liệu.",
    "If you have already arranged filing through another firm, please advise us so we may update our records accordingly.":
        "Nếu bạn đã thuê công ty khác khai thuế, vui lòng thông báo cho chúng tôi để cập nhật hồ sơ.",
    "This communication is intended solely for the use of the named recipient. It may contain confidential or privileged information. If you have received this message in error, please notify us immediately and do not copy, distribute, or act upon its contents.":
        "Thông tin này chỉ dành cho người nhận được chỉ định. Có thể chứa thông tin bảo mật hoặc đặc quyền. Nếu bạn nhận sai tin nhắn này, vui lòng thông báo ngay và không sao chép, phân phối hoặc sử dụng nội dung của nó.",
}


def replace_paragraph_text(para, new_text):
    """Replace all text in a paragraph while preserving first run's formatting."""
    if not para.runs:
        return
    first_run = para.runs[0]
    for run in para.runs:
        run.text = ""
    first_run.text = new_text


def create_translated_template(src_path, dst_path, translations, lang_label):
    """Load ENG template, translate paragraphs, save as new file."""
    doc = Document(src_path)

    for para in doc.paragraphs:
        text = para.text.strip()
        if text in translations:
            replace_paragraph_text(para, translations[text])

    doc.save(dst_path)
    print(f"  Created: {os.path.basename(dst_path)}")


def main():
    print("Creating T2 translated templates...\n")

    for rn in [1, 2, 3]:
        eng_path = os.path.join(TPL_DIR, f"T2 Reminder - R{rn} - ENG.docx")
        if not os.path.exists(eng_path):
            print(f"  WARNING: {eng_path} not found, skipping R{rn}")
            continue

        chn_path = os.path.join(TPL_DIR, f"T2 Reminder - R{rn} - CHN.docx")
        create_translated_template(eng_path, chn_path, CHN, "CHN")

        vie_path = os.path.join(TPL_DIR, f"T2 Reminder - R{rn} - VIE.docx")
        create_translated_template(eng_path, vie_path, VIE, "VIE")

    print("\nDone! All T2 templates created.")


if __name__ == "__main__":
    main()
