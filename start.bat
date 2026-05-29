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

:: Activer le venv (.bat — compatible CMD, pas PowerShell)
echo [1/3] Activation de l'environnement...
if not exist .venv\Scripts\activate.bat (
    echo ERREUR : venv introuvable. Lance d'abord :
    echo   python -m venv .venv
    echo   .venv\Scripts\activate.bat
    echo   python -m pip install -r requirements.txt
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

:: Vérifier le .env
if not exist .env (
    echo ERREUR : fichier .env manquant.
    echo Copie .env.example en .env et remplis ANTHROPIC_API_KEY
    pause
    exit /b 1
)

:: Créer les dossiers si absents
if not exist data mkdir data
if not exist static mkdir static

echo [2/3] Mise a jour depuis GitHub...
git pull

echo.
echo [3/3] Lancement de Bigdoc...
echo.
echo  ----------------------------------------
echo  Bigdoc tourne sur http://localhost:8000
echo  Admin : http://localhost:8000/admin
echo  CTRL+C pour arreter
echo  ----------------------------------------
echo.

:: Ouvrir le navigateur apres 2 secondes
start /b cmd /c "timeout /t 2 >nul && start http://localhost:8000"

:: Lancer le serveur
python -m uvicorn main:app --reload --port 8000
