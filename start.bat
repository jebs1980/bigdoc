@echo off
title Bigdoc — Démarrage
color 0A

echo.
echo  ██████╗ ██╗ ██████╗ ██████╗  ██████╗  ██████╗
echo  ██╔══██╗██║██╔════╝ ██╔══██╗██╔═══██╗██╔════╝
echo  ██████╔╝██║██║  ███╗██║  ██║██║   ██║██║
echo  ██╔══██╗██║██║   ██║██║  ██║██║   ██║██║
echo  ██████╔╝██║╚██████╔╝██████╔╝╚██████╔╝╚██████╗
echo  ╚═════╝ ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝  ╚═════╝
echo.
echo  un service RMS
echo  ----------------------------------------
echo.

:: Aller sur le NAS
Z:
cd Z:\

:: Activer le venv
echo [1/3] Activation de l'environnement...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERREUR : venv introuvable. Lance install.bat d'abord.
    pause
    exit /b 1
)

:: Vérifier le .env
if not exist .env (
    echo ERREUR : fichier .env manquant.
    echo Copie .env.example en .env et remplis ANTHROPIC_API_KEY
    pause
    exit /b 1
)

:: Créer le dossier data si absent
if not exist data mkdir data

:: Créer le dossier static si absent
if not exist static mkdir static

echo [2/3] Mise a jour depuis GitHub...
git pull

echo.
echo [3/3] Lancement de Bigdoc...
echo.
echo  ----------------------------------------
echo  Bigdoc tourne sur http://localhost:8000
echo  CTRL+C pour arreter
echo  ----------------------------------------
echo.

:: Ouvrir le navigateur après 2 secondes
start /b cmd /c "timeout /t 2 >nul && start http://localhost:8000"

:: Lancer le serveur
python -m uvicorn main:app --reload --port 8000
