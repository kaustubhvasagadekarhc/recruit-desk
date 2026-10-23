; Author: kaustubh.s.v
; Recruit Desk prerequisite pages, included by windows/installer.nsi right
; after the Welcome page. Order: Node.js and npm, Claude Code, Claude login, uv.
; Next stays disabled until the step's check passes, and the leave function
; refuses to move on otherwise, so no app file is copied before every check
; is green. Cancel leaves without installing anything. Checks run hidden
; through rd-prereqs.ps1 with a timeout; installs run in a visible console
; window so the page never waits on them, and a timer notices when they end.
!include nsDialogs.nsh
!include LogicLib.nsh

!define RD_CHECK_TIMEOUT 45000 ; ms; `claude auth status` can be slow on first run
!define RD_TIMER_MS 3000

Var RD_Step      ; node | claude | login | uv
Var RD_Title
Var RD_Intro
Var RD_ActionText
Var RD_Manual
Var RD_Ok        ; 1 when the current step passed
Var RD_Busy      ; 1 while an install or login window is open
Var RD_PS        ; path of powershell.exe
Var RD_Status
Var RD_Detail
Var RD_Action
Var RD_Check
Var RD_Font

Function RD_Prepare
  InitPluginsDir
  IfFileExists "$PLUGINSDIR\rd-prereqs.ps1" +2
    File "/oname=$PLUGINSDIR\rd-prereqs.ps1" "${RD_PREREQ_SCRIPT}"
  ; Use the 64-bit PowerShell from this 32-bit installer when there is one.
  StrCpy $RD_PS "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  IfFileExists $RD_PS +2
    StrCpy $RD_PS "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
FunctionEnd

; Runs the hidden check for $RD_Step. Out: $RD_Ok, $R9 = result text or "timeout".
Function RD_RunCheck
  nsExec::ExecToStack /TIMEOUT=${RD_CHECK_TIMEOUT} '"$RD_PS" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\rd-prereqs.ps1" check $RD_Step'
  Pop $R8
  Pop $R9
  ${If} $R8 == "0"
    StrCpy $RD_Ok 1
  ${Else}
    StrCpy $RD_Ok 0
    ${If} $R8 == "timeout"
      StrCpy $R9 "timeout"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function RD_SetNext
  GetDlgItem $0 $HWNDPARENT 1
  EnableWindow $0 $RD_Ok
FunctionEnd

; Re-checks and shows OK / installing / failed / missing in plain language.
Function RD_Refresh
  ${NSD_SetText} $RD_Status "Checking..."
  EnableWindow $RD_Check 0
  Call RD_RunCheck
  EnableWindow $RD_Check 1
  ${If} $RD_Ok == 1
    StrCpy $RD_Busy 0
    Delete "$PLUGINSDIR\$RD_Step.running"
    ${NSD_SetText} $RD_Status "OK: $R9"
    ${NSD_SetText} $RD_Detail "This step is done. Press Next to continue."
    EnableWindow $RD_Action 0
  ${ElseIf} ${FileExists} "$PLUGINSDIR\$RD_Step.running"
    StrCpy $RD_Busy 1
    ${If} $RD_Step == "login"
      ${NSD_SetText} $RD_Status "Waiting for you to sign in..."
      ${NSD_SetText} $RD_Detail "Finish signing in in the browser and the Claude window. This page updates by itself; you can also press Check again. To run it yourself, open a terminal and run:"
    ${Else}
      ${NSD_SetText} $RD_Status "Installing..."
      ${NSD_SetText} $RD_Detail "A separate window is doing the install; Windows may ask for permission. This page updates by itself when it finishes. If you closed that window, press the install button again. To do it by hand, run this in PowerShell:"
    ${EndIf}
    EnableWindow $RD_Action 1
  ${ElseIf} ${FileExists} "$PLUGINSDIR\$RD_Step.failed"
    StrCpy $RD_Busy 0
    ${NSD_SetText} $RD_Status "Failed: the install did not finish."
    ${NSD_SetText} $RD_Detail "Run this command by hand in PowerShell, then press Check again:"
    EnableWindow $RD_Action 1
  ${ElseIf} $R9 == "timeout"
    ${NSD_SetText} $RD_Status "The check took too long."
    ${NSD_SetText} $RD_Detail "Press Check again. If it keeps happening, run this in PowerShell to see what is wrong:"
    EnableWindow $RD_Action 1
  ${Else}
    ${If} $RD_Step == "login"
      ${NSD_SetText} $RD_Status "Not signed in to Claude yet."
      ${NSD_SetText} $RD_Detail "Press $\"$RD_ActionText$\", sign in in the browser, then press Check again. Or run this yourself in a terminal:"
    ${Else}
      ${NSD_SetText} $RD_Status "Missing: not found on this computer."
      ${NSD_SetText} $RD_Detail "Press $\"$RD_ActionText$\", or run this in PowerShell yourself and then press Check again:"
    ${EndIf}
    EnableWindow $RD_Action 1
  ${EndIf}
  Call RD_SetNext
FunctionEnd

Function RD_OnCheck
  Pop $0
  Call RD_Refresh
FunctionEnd

Function RD_OnAction
  Pop $0
  Delete "$PLUGINSDIR\$RD_Step.failed"
  FileOpen $1 "$PLUGINSDIR\$RD_Step.running" w
  FileWrite $1 "1"
  FileClose $1
  ${If} $RD_Step == "login"
    Exec '"$RD_PS" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\rd-prereqs.ps1" login'
  ${Else}
    Exec '"$RD_PS" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\rd-prereqs.ps1" install $RD_Step'
  ${EndIf}
  StrCpy $RD_Busy 1
  Call RD_Refresh
FunctionEnd

; While an install window is open, wait for it to remove its .running file.
; For the login step, re-check on every tick: the user may stay in Claude.
Function RD_OnTimer
  ${If} $RD_Busy == 1
    ${If} $RD_Step == "login"
    ${OrIfNot} ${FileExists} "$PLUGINSDIR\$RD_Step.running"
      Call RD_Refresh
    ${EndIf}
  ${EndIf}
FunctionEnd

Function RD_FirstCheck
  ${NSD_KillTimer} RD_FirstCheck
  Call RD_Refresh
FunctionEnd

Function RD_CreatePage
  !insertmacro MUI_HEADER_TEXT "$RD_Title" "Recruit Desk needs this before it can be installed."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 30u "$RD_Intro"
  Pop $0
  ${NSD_CreateLabel} 0 36u 100% 20u "Checking..."
  Pop $RD_Status
  CreateFont $RD_Font "$(^Font)" 10 700
  SendMessage $RD_Status ${WM_SETFONT} $RD_Font 0
  ${NSD_CreateButton} 0 60u 120u 16u "$RD_ActionText"
  Pop $RD_Action
  ${NSD_OnClick} $RD_Action RD_OnAction
  ${NSD_CreateButton} 128u 60u 70u 16u "Check again"
  Pop $RD_Check
  ${NSD_OnClick} $RD_Check RD_OnCheck
  ${NSD_CreateLabel} 0 84u 100% 26u ""
  Pop $RD_Detail
  ${NSD_CreateText} 0 112u 100% 13u "$RD_Manual"
  Pop $0
  SendMessage $0 ${EM_SETREADONLY} 1 0
  StrCpy $RD_Ok 0
  Call RD_SetNext
  ; Show the page first, then run the (hidden, time-limited) check.
  ${NSD_CreateTimer} RD_FirstCheck 100
  ${NSD_CreateTimer} RD_OnTimer ${RD_TIMER_MS}
  nsDialogs::Show
  ${NSD_KillTimer} RD_OnTimer
  ${NSD_KillTimer} RD_FirstCheck
FunctionEnd

Function RD_LeavePage
  ${If} $RD_Ok != 1
    MessageBox MB_OK|MB_ICONINFORMATION "This step has not passed yet. Recruit Desk is not installed until it does. Fix it, press Check again, or press Cancel to leave without installing."
    Abort
  ${EndIf}
FunctionEnd

!macro RD_PREREQ_PAGE ID TITLE INTRO ACTION MANUAL
  Page custom RD_Show_${ID} RD_LeavePage
  Function RD_Show_${ID}
    ${If} $PassiveMode = 1
      Abort
    ${EndIf}
    Call RD_Prepare
    StrCpy $RD_Step "${ID}"
    StrCpy $RD_Title "${TITLE}"
    StrCpy $RD_Intro "${INTRO}"
    StrCpy $RD_ActionText "${ACTION}"
    StrCpy $RD_Manual "${MANUAL}"
    StrCpy $RD_Busy 0
    !if "${ID}" == "login"
      ; Already signed in: skip this page without showing it.
      Call RD_RunCheck
      ${If} $RD_Ok == 1
        Abort
      ${EndIf}
    !endif
    Call RD_CreatePage
  FunctionEnd
!macroend

!insertmacro RD_PREREQ_PAGE node "Step 1 of 4: Node.js and npm" \
  "Recruit Desk uses Node.js and npm to install Claude Code. The installer checks that $\"node -v$\" and $\"npm -v$\" work." \
  "Install Node.js LTS" \
  "Download the LTS installer from https://nodejs.org, run it, then press Check again (no winget needed)"
!insertmacro RD_PREREQ_PAGE claude "Step 2 of 4: Claude Code" \
  "The AI assistants run inside Claude Code. The installer checks that $\"claude --version$\" works." \
  "Install Claude Code" \
  "npm install -g @anthropic-ai/claude-code"
!insertmacro RD_PREREQ_PAGE login "Step 3 of 4: Sign in to Claude" \
  "Claude Code needs your Claude account. The button opens a window running Claude; sign in in the browser when it asks." \
  "Log in to Claude" \
  "claude"
!insertmacro RD_PREREQ_PAGE uv "Step 4 of 4: uv" \
  "The assistants run their small Python scripts with uv. The installer checks that $\"uv --version$\" works." \
  "Install uv" \
  "powershell -ExecutionPolicy ByPass -c $\"irm https://astral.sh/uv/install.ps1 | iex$\""
