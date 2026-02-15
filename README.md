# Ön Muhasebe Uygulaması

Flask + SQLite ile geliştirilmiş basit bir ön muhasebe paneli.

## Özellikler

- Cari kartı ve bakiye takibi
- Girdi/çıktı faturaları ve tek kalem detay girişi
- Ödeme/tahsilat kayıtları (nakit, kredi kartı, havale, EFT, çek, senet)
- Çek/senet portföyü
- Ay sonu raporu (satış, alış, tahsilat, ödeme, brüt kâr, net nakit)
- Excel ile uyumlu CSV dışa aktarımları

## Kurulum

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Ardından tarayıcıdan `http://localhost:5000` açın.
