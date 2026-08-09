# Imager — manifest plików do ChatGPT Projects / Custom GPT

Środowisko ChatGPT Projects na iOS traktuj jako płaską przestrzeń plików. Do projektu wgraj maksymalnie poniższe pliki — bez folderów:

1. `CHATGPT_PROJECT_INSTRUCTIONS.md` — główne instrukcje projektu.
2. `IMAGER_IMAGE_PROMPTS.md` — szablony dla natywnego @Stwórz Obraz.
3. `ada_upscale_a4_pdf.py` — kod Ada/Code Interpreter do upscale x8 i PDF A4.
4. `self_engine.py` — opcjonalny deterministyczny silnik geometrii, jeśli środowisko pozwala na Python + Pillow/OpenCV.
5. `README.md` — opis techniczny i lokalne uruchomienie.

Custom GPT nie wymaga OpenAPI Actions dla podstawowego wariantu, ponieważ proces działa na plikach użytkownika, natywnym generowaniu obrazów i Code Interpreter. Jeśli dodasz własny publiczny endpoint, dopiero wtedy dołącz schemat Actions.
