"""PDF-выписки → txt 1CClientBankExchange. Обычно запускается из команды bs автоматически."""
from pathlib import Path

from django.core.management.base import BaseCommand

from treasury.services.pdf_statements import convert_folder

from .bs import PDF_OUT


class Command(BaseCommand):
    help = "Делает txt в формате 1С из PDF-выписок (Локо, СберБизнес, Совкомбанк)"

    def add_arguments(self, parser):
        parser.add_argument("folder", help="папка с PDF (подпапки и zip тоже читаются)")
        parser.add_argument("--out", help=f"куда писать txt; по умолчанию <папка>/{PDF_OUT}")

    def handle(self, *args, **opts):
        src = Path(opts["folder"]).expanduser().resolve()
        out = Path(opts["out"]).expanduser().resolve() if opts["out"] else src / PDF_OUT
        convert_folder(src, out, log=self.stdout.write)
        self.stdout.write(f"Готово: {out}")
