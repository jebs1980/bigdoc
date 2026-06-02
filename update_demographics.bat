@echo off
title Bigdoc — Mise à jour démographie médicale
color 0A

echo.
echo  BIGDOC — Mise à jour mensuelle
echo  Données démographiques médicales
echo  ─────────────────────────────────
echo.

Z:
cd Z:\

:: Activer le venv
call .venv\Scripts\activate.bat

echo [1/2] Téléchargement et traitement des données RPPS...
python scripts\update_demographics.py

echo.
echo [2/2] Redémarrage du serveur pour recharger les données...
echo  (Ferme et relance manuellement si le serveur tourne)
echo.
echo  ✅ Démographie mise à jour — restart bigdoc pour appliquer
echo.
pause
