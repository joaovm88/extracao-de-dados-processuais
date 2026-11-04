import io

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def make_pdf(path, image_page=True):
    c = canvas.Canvas(str(path), pagesize=A4)
    pages = [
        ["1ª VARA CÍVEL DA COMARCA DE SÃO PAULO", "Processo nº 1234567-89.2023.8.26.0100",
         "Exequente: Banco Exemplo S.A.", "Executado: Empresa Alfa Ltda", "CNPJ 11.222.333/0001-81",
         "Valor da causa: R$ 150.000,00"],
        ["Foi realizada a penhora online via SISBAJUD.", "Restou bloqueado o valor de R$ 48.320,15 na conta do executado.",
         "Processo 1234567-89.2023.8.26.0100"],
        ["Comprovante de recolhimento de DARF no valor de R$ 1.250,40 junto a Receita Federal.",
         "Custas processuais: R$ 980,00."],
    ]
    for lines in pages:
        y = 800
        for ln in lines:
            c.drawString(60, y, ln)
            y -= 22
        c.showPage()
    if image_page:  # página só imagem (simula digitalização)
        img = Image.new("RGB", (800, 400), "white")
        ImageDraw.Draw(img).text((20, 20), "Guia escaneada", fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        c.drawImage(ImageReader(buf), 50, 400, 500, 250)
        c.showPage()
    c.save()
