@echo off
rem 훕스 공지비서 실행 (작업 스케줄러에 등록해 하루 몇 차례 돌리면 됨)
chcp 65001 >nul
cd /d "%~dp0"
python run.py %*
