@echo off
rem 훕스 공지비서 실행 후 GitHub Pages로 올림 (작업 스케줄러가 평일 9·13·17시에 실행)
chcp 65001 >nul
cd /d "%~dp0"
python run.py --publish %*
