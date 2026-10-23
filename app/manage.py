"""Admin commands.

    python -m app.manage add-user <ten_dang_nhap> "<Họ tên>" [--role ipc|qa]
    python -m app.manage list-templates
"""
import argparse
import getpass
import sys

from app import db
from app.templates_loader import load_templates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add-user", help="Tạo người dùng")
    add.add_argument("name")
    add.add_argument("full_name")
    add.add_argument("--role", default="ipc", choices=["ipc", "qa"])
    add.add_argument("--pin", help="PIN (bỏ trống để nhập ẩn)")
    sub.add_parser("list-templates", help="Kiểm tra các file biểu mẫu YAML")
    args = parser.parse_args(argv)

    db.init_db()
    if args.cmd == "add-user":
        pin = args.pin or getpass.getpass("PIN (4-8 số): ")
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            print("PIN phải gồm 4-8 chữ số", file=sys.stderr)
            return 1
        db.create_user(args.name, args.full_name, pin, args.role)
        print(f"Đã tạo người dùng {args.name} ({args.role})")
    elif args.cmd == "list-templates":
        for tpl in load_templates().values():
            print(f"{tpl.code}: {tpl.name} – {len(tpl.header_fields)} trường, {len(tpl.columns)} cột")
    return 0


if __name__ == "__main__":
    sys.exit(main())
