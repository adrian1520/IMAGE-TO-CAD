# IMAGER image prompts

## Geometry-only prompt for @Stwórz Obraz

Utwórz czystą techniczną wizualizację 1:1 mini-CAD na podstawie dostarczonego zdjęcia/skanu. Zachowaj geometrię, proporcje i względne położenia ścian, drzwi, okien, łuków, okręgów, symboli oraz układu pomieszczeń. Usuń zagniecenia papieru, cienie, szum, plamy, przebarwienia, perspektywę aparatu i falowanie kartki.

Tekst nie jest źródłem geometrii i nie jest wymagany do wiernego odtworzenia na tym etapie. Nie próbuj interpretować, poprawiać ani przepisywać opisów przy symbolach. Nie generuj nowych opisów.

Wynik: czysty czarno-biały rysunek CAD-like na białym tle, bez tekstury papieru i bez elementów dodanych przez model.

## Negative instruction

Do not invent, rewrite, normalize, or visually hallucinate labels. Symbol descriptions are recovered later from the original image by the OCR pipeline.
# Prompty dla @Stwórz Obraz — rekonstrukcja 1:1 mini-CAD

## Prompt bazowy
Utwórz czystą, techniczną wizualizację 1:1 mini-CAD na podstawie dostarczonego zdjęcia/skanu. Zachowaj wszystkie istotne proporcje, położenia, kąty, grubości linii, układ ścian, łuki, okręgi, drzwi, okna, symbole i tekst w możliwie najwierniejszej pozycji. Usuń wyłącznie artefakty fotograficzne: zagniecenia papieru, cienie, plamy, szum, przebarwienia, perspektywę aparatu i falowanie kartki. Wynik ma wyglądać jak czysty czarno-biały rysunek CAD na białym tle, bez tekstury papieru, bez ozdobników, bez nowych elementów, bez interpretacyjnego dopowiadania brakujących części.

## Negatywne wymagania
Nie dodawaj nowych pomieszczeń, wymiarów, opisów ani symboli. Nie zmieniaj układu funkcjonalnego. Nie stylizuj jako render 3D. Nie wygładzaj geometrii kosztem przesunięcia położeń. Nie zamieniaj łuków ani okręgów na wielokąty. Nie zostawiaj zagnieceń, cieni, tła biurka ani perspektywy zdjęcia.

## Kontrola jakości po wygenerowaniu
Sprawdź: czy ściany pozostają proste i równoległe, czy zamknięcia pomieszczeń są zachowane, czy drzwi i okna są na tych samych miejscach, czy tekst nie został przeniesiony, czy proporcje całej kartki odpowiadają oryginałowi.
