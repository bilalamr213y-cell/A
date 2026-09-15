from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(user_id: int, admin_id: int, is_running: bool, email_count: int, max_emails: int):
    burn_text = "Stop Burn Recovery" if is_running else "Burn Recovery"
    burn_cb = "btn_stop_burn" if is_running else "btn_start_burn"

    keyboard = [
        [InlineKeyboardButton(burn_text, callback_data=burn_cb)],
        [
            InlineKeyboardButton("Add Account (Token)", callback_data="btn_add_account"),
            InlineKeyboardButton("My Accounts", callback_data="btn_my_accounts"),
        ],
        [InlineKeyboardButton("Control Account", callback_data="btn_control_menu")],
        [
            InlineKeyboardButton(f"Emails ({email_count}/{max_emails})", callback_data="btn_emails_menu"),
            InlineKeyboardButton("Send OTP", callback_data="btn_send_otp"),
        ],
        [
            InlineKeyboardButton("Status", callback_data="btn_status"),
            InlineKeyboardButton("Referral", callback_data="btn_referral"),
        ],
    ]

    if user_id == admin_id:
        keyboard.append([InlineKeyboardButton("Admin Panel", callback_data="btn_admin")])

    return InlineKeyboardMarkup(keyboard)


def emails_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Email", callback_data="btn_email_add")],
        [InlineKeyboardButton("Delete Email", callback_data="btn_email_delete")],
        [InlineKeyboardButton("View List", callback_data="btn_email_view")],
        [InlineKeyboardButton("Back", callback_data="btn_main_menu")],
    ])


def delete_email_menu(emails: list):
    keyboard = []
    for idx, email in enumerate(emails):
        label = email if len(email) <= 30 else email[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"X {label}", callback_data=f"email_del_{idx}")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="btn_emails_menu")])
    return InlineKeyboardMarkup(keyboard)


def accounts_menu(accounts: list):
    keyboard = []
    for idx, acc in enumerate(accounts[:15]):
        email = acc.get("email", "?")
        token_short = acc.get("token", "")[:8]
        label = f"{email} ({token_short}...)"
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"acc_select_{idx}")])
    keyboard.append([InlineKeyboardButton("Back", callback_data="btn_main_menu")])
    return InlineKeyboardMarkup(keyboard)


def control_menu(token: str):
    token_short = token[:8]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Show Full Info", callback_data=f"ctrl_info_{token_short}")],
        [InlineKeyboardButton("Send OTP to Email", callback_data=f"ctrl_otp_{token_short}")],
        [InlineKeyboardButton("Burn This Account", callback_data=f"ctrl_burn_{token_short}")],
        [InlineKeyboardButton("Remove Account", callback_data=f"ctrl_del_{token_short}")],
        [InlineKeyboardButton("Back", callback_data="btn_my_accounts")],
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Credits", callback_data="admin_add_credits")],
        [InlineKeyboardButton("Back", callback_data="btn_main_menu")],
    ])


def back_to_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Back to Main Menu", callback_data="btn_main_menu")]
    ])


def back_to_accounts():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Back", callback_data="btn_my_accounts")]
    ])