#!/usr/bin/env python3
# coding: utf-8
"""把 i18n/<lang>/LC_MESSAGES/messages.po 编译为同名 messages.mo。

本仓库的运行环境可能没有 GNU gettext 的 msgfmt，这里改用纯 Python 的 polib
实现，保证 .po 修改后能一条命令重编译 .mo：

    python tools/build_i18n.py

依赖: polib（见 requirements-dev.txt）。
"""
import os
import sys

try:
    import polib
except ImportError:  # pragma: no cover - 环境缺依赖时给出明确提示
    sys.exit("缺少依赖 polib，请先执行: pip install polib")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N_DIR = os.path.join(ROOT, 'i18n')


def main():
    if not os.path.isdir(I18N_DIR):
        sys.exit("找不到 i18n 目录: {0}".format(I18N_DIR))

    compiled = 0
    for lang in sorted(os.listdir(I18N_DIR)):
        po_path = os.path.join(I18N_DIR, lang, 'LC_MESSAGES', 'messages.po')
        if not os.path.isfile(po_path):
            continue
        mo_path = os.path.splitext(po_path)[0] + '.mo'
        catalog = polib.pofile(po_path)
        catalog.save_as_mofile(mo_path)
        print("compiled {0} -> {1} ({2} entries)".format(
            os.path.relpath(po_path, ROOT), os.path.relpath(mo_path, ROOT), len(catalog)))
        compiled += 1

    if not compiled:
        sys.exit("在 {0} 下未找到任何 messages.po".format(I18N_DIR))


if __name__ == '__main__':
    main()
