; Author: kaustubh.s.v
; Recruit Desk "Exa search key" page and the assistant-folder install steps.
; windows/installer.nsi includes this right after the prerequisite pages.
; The key is kept in a variable only until it is written into the .env of
; every assistant: it is never printed, logged or put on a command line.
!include nsDialogs.nsh
!include LogicLib.nsh

!define RD_EXA_URL "https://dashboard.exa.ai"
!define RD_UV_TIMEOUT 600000 ; ms per assistant; the first `uv sync` downloads Python

Var RD_AgentsDir ; where the assistants are installed (user-writable)
Var RD_ExaKey    ; the key typed on the page, empty when skipped
Var RD_ExaField
Var RD_ExaSkip

Page custom RD_ShowExa RD_LeaveExa

Function RD_DefaultAgentsDir
  ${If} $RD_AgentsDir == ""
    StrCpy $RD_AgentsDir "$PROFILE\Documents\Recruit Desk\AI-AGENTS"
  ${EndIf}
FunctionEnd

Function RD_ShowExa
  ${If} $PassiveMode = 1
    Abort
  ${EndIf}
  Call RD_DefaultAgentsDir
  !insertmacro MUI_HEADER_TEXT "Exa search key (optional)" "Lets the assistants search the web. You can leave it empty."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 44u "Optional. A free Exa key lets the assistants search the web for candidates and public job posts; without it, company job feeds and career pages still work. To add one, open the Exa dashboard, sign up for free, copy your API key and paste it below. The key is saved only on this computer, in the assistants' settings files (.env)."
  Pop $0
  ${NSD_CreateLink} 0 46u 100% 12u "${RD_EXA_URL}"
  Pop $0
  ${NSD_OnClick} $0 RD_OpenExa
  ${NSD_CreateLabel} 0 64u 100% 10u "Exa API key:"
  Pop $0
  ${NSD_CreatePassword} 0 76u 100% 13u ""
  Pop $RD_ExaField
  ${NSD_CreateCheckbox} 0 96u 100% 12u "Skip, add later (Recruit Desk asks for it on its setup screen)"
  Pop $RD_ExaSkip
  ${NSD_CreateLabel} 0 114u 100% 22u "If you saved a key in an earlier install, leave the box empty to keep it. The assistants will be installed to: $RD_AgentsDir"
  Pop $0
  nsDialogs::Show
FunctionEnd

Function RD_OpenExa
  Pop $0
  ExecShell "open" "${RD_EXA_URL}"
FunctionEnd

Function RD_LeaveExa
  ${NSD_GetText} $RD_ExaField $RD_ExaKey
  ${NSD_GetState} $RD_ExaSkip $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $RD_ExaKey ""
  ${EndIf}
FunctionEnd

; Replace the EXA_API_KEY line in $R0\.env, or start the .env from
; .env.example. Every other line is kept as it is. In: $R0 = assistant folder.
Function RD_WriteExaKey
  StrCpy $R1 "$R0\.env"
  IfFileExists $R1 +2
    StrCpy $R1 "$R0\.env.example"
  StrCpy $R5 0
  FileOpen $R3 "$R0\.env.rdnew" w
  IfErrors rd_wk_fail
  IfFileExists $R1 0 rd_wk_append
  FileOpen $R2 $R1 r
  rd_wk_loop:
    ClearErrors
    FileRead $R2 $R4
    IfErrors rd_wk_eof
    StrCpy $R6 $R4 12
    ${If} $R6 == "EXA_API_KEY="
      FileWrite $R3 "EXA_API_KEY=$RD_ExaKey$\r$\n"
      StrCpy $R5 1
    ${Else}
      FileWrite $R3 $R4
    ${EndIf}
    Goto rd_wk_loop
  rd_wk_eof:
  FileClose $R2
  rd_wk_append:
  ${If} $R5 == 0
    FileWrite $R3 "$\r$\nEXA_API_KEY=$RD_ExaKey$\r$\n"
  ${EndIf}
  FileClose $R3
  ; MOVEFILE_REPLACE_EXISTING: the old .env stays until the new one is complete.
  System::Call 'kernel32::MoveFileExW(w "$R0\.env.rdnew", w "$R0\.env", i 1) i .r9'
  ${If} $9 == 0
    Delete "$R0\.env.rdnew"
    DetailPrint "Could not save the Exa key in $R0. Add it in Recruit Desk's setup screen."
  ${EndIf}
  Return
  rd_wk_fail:
  DetailPrint "Could not save the Exa key in $R0. Add it in Recruit Desk's setup screen."
FunctionEnd

; Optional: prepare each assistant's Python packages so the first run is fast.
; Any failure is skipped quietly; the app's setup screen offers it again.
Function RD_UvSync
  InitPluginsDir
  File "/oname=$PLUGINSDIR\rd-agents.ps1" "${RD_AGENTS_SCRIPT}"
  StrCpy $R7 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  IfFileExists $R7 +2
    StrCpy $R7 "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
  ${ForEach} $R8 1 3 + 1
    ${If} $R8 == 1
      StrCpy $R0 "$RD_AgentsDir\candidate-agent"
    ${ElseIf} $R8 == 2
      StrCpy $R0 "$RD_AgentsDir\company-agent"
    ${Else}
      StrCpy $R0 "$RD_AgentsDir\account-agent"
    ${EndIf}
    DetailPrint "Preparing $R0 (uv sync; this can take a few minutes)..."
    nsExec::ExecToStack /TIMEOUT=${RD_UV_TIMEOUT} '"$R7" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\rd-agents.ps1" -Dir "$R0"'
    Pop $0
    Pop $1
    ${If} $0 != 0
      DetailPrint "Skipped: Recruit Desk will offer to prepare it on its setup screen."
    ${EndIf}
  ${Next}
FunctionEnd

; Copy the assistants. The bundle holds code and config only, so the user's
; .env, memory.db, workbooks and runs/ are never overwritten or removed.
Function RD_InstallAgents
  Call RD_DefaultAgentsDir
  DetailPrint "Installing the assistants to $RD_AgentsDir"
  SetOverwrite on
  SetOutPath "$RD_AgentsDir\candidate-agent"
  File /r "${RD_AGENTS_BUNDLE}/candidate-agent/*"
  SetOutPath "$RD_AgentsDir\company-agent"
  File /r "${RD_AGENTS_BUNDLE}/company-agent/*"
  SetOutPath "$RD_AgentsDir\account-agent"
  File /r "${RD_AGENTS_BUNDLE}/account-agent/*"
  ; Tell the app where they are (read by settings::detect_agents_root).
  FileOpen $0 "$INSTDIR\agents-root.txt" w
  FileWriteUTF16LE /BOM $0 "$RD_AgentsDir"
  FileClose $0
  ${If} $RD_ExaKey != ""
    StrCpy $R0 "$RD_AgentsDir\candidate-agent"
    Call RD_WriteExaKey
    StrCpy $R0 "$RD_AgentsDir\company-agent"
    Call RD_WriteExaKey
    StrCpy $R0 "$RD_AgentsDir\account-agent"
    Call RD_WriteExaKey
    StrCpy $RD_ExaKey ""
    DetailPrint "Saved the Exa key for all three assistants."
  ${EndIf}
!ifdef RD_APOLLO_PAGE
  Call RD_InstallApolloKey
!endif
  Call RD_UvSync
  SetOutPath "$INSTDIR"
FunctionEnd
