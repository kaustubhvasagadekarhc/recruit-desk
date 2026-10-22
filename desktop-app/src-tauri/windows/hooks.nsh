; Author: kaustubh.s.v
; Recruit Desk NSIS installer hooks. Tauri includes this file near the top of
; windows/installer.nsi. It records where the prerequisite pages, the Exa key
; page and the staged assistant folders live; the template includes the pages
; right after the Welcome page.
!define RD_PREREQ_PAGES "${__FILEDIR__}/prereq-pages.nsh"
!define RD_PREREQ_SCRIPT "${__FILEDIR__}/rd-prereqs.ps1"
!define RD_EXA_PAGE "${__FILEDIR__}/exa-page.nsh"
!define RD_APOLLO_PAGE "${__FILEDIR__}/apollo-page.nsh"
!define RD_AGENTS_SCRIPT "${__FILEDIR__}/rd-agents.ps1"
; Filled by scripts/stage-agents.mjs (npm run build); code and config only.
!define RD_AGENTS_BUNDLE "${__FILEDIR__}/agents-bundle"

!macro NSIS_HOOK_POSTINSTALL
  Call RD_InstallAgents
  ; The results stay on this computer: Documents\RecruitDesk-Data, one folder per assistant, each holding that assistant's Excel file.
  CreateDirectory "$DOCUMENTS\RecruitDesk-Data\Find candidates"
  CreateDirectory "$DOCUMENTS\RecruitDesk-Data\Company jobs"
  CreateDirectory "$DOCUMENTS\RecruitDesk-Data\Accounts"
!macroend

; Uninstall removes the app only. The assistants folder holds the user's
; workbooks, databases and .env, so it is left in place and the user is told.
!macro NSIS_HOOK_POSTUNINSTALL
  ClearErrors
  FileOpen $0 "$INSTDIR\agents-root.txt" r
  ${IfNot} ${Errors}
    FileReadUTF16LE $0 $1
    FileClose $0
    Delete "$INSTDIR\agents-root.txt"
    RMDir "$INSTDIR"
    DetailPrint "Kept your assistants folder: $1"
    DetailPrint "Kept your results folder: $DOCUMENTS\RecruitDesk-Data"
    ${If} $UpdateMode <> 1
      MessageBox MB_OK|MB_ICONINFORMATION "Recruit Desk was removed. Your assistants folder was left in place, with your databases and keys:$\r$\n$\r$\n$1$\r$\n$\r$\nYour Excel results were left in:$\r$\n$\r$\n$DOCUMENTS\RecruitDesk-Data$\r$\n$\r$\nDelete them yourself if you no longer need them." /SD IDOK
    ${EndIf}
  ${EndIf}
!macroend
