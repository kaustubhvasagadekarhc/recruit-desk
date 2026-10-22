; Recruit Desk "Apollo API key" page (optional, paid; account research only).
; windows/installer.nsi includes this right after the Exa key page.
; The key is kept in a variable only until it is written into
; account-agent\.env: it is never printed, logged or put on a command line.
!include nsDialogs.nsh
!include LogicLib.nsh

!define RD_APOLLO_URL "https://app.apollo.io/#/settings/integrations/api"

Var RD_ApolloKey   ; the key typed on the page, empty when skipped
Var RD_ApolloField
Var RD_ApolloSkip

Page custom RD_ShowApollo RD_LeaveApollo

Function RD_ShowApollo
  ${If} $PassiveMode = 1
    Abort
  ${EndIf}
  Call RD_DefaultAgentsDir
  !insertmacro MUI_HEADER_TEXT "Apollo API key (optional, paid)" "Adds verified leader emails to account research. You can leave it empty."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 44u "Optional. With an Apollo.io API key, account research asks Apollo first for a company's leaders and keeps only the emails Apollo marks verified (at most 6 credits per company, answers cached for 30 days). Without it, account research uses free public sources only. The key is saved only on this computer, in the account assistant's settings file (.env)."
  Pop $0
  ${NSD_CreateLink} 0 46u 100% 12u "${RD_APOLLO_URL}"
  Pop $0
  ${NSD_OnClick} $0 RD_OpenApollo
  ${NSD_CreateLabel} 0 64u 100% 10u "Apollo API key:"
  Pop $0
  ${NSD_CreatePassword} 0 76u 100% 13u ""
  Pop $RD_ApolloField
  ${NSD_CreateCheckbox} 0 96u 100% 12u "Skip, add later (Recruit Desk Settings, API keys)"
  Pop $RD_ApolloSkip
  ${NSD_CreateLabel} 0 114u 100% 22u "If you saved a key in an earlier install, leave the box empty to keep it."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function RD_OpenApollo
  Pop $0
  ExecShell "open" "${RD_APOLLO_URL}"
FunctionEnd

Function RD_LeaveApollo
  ${NSD_GetText} $RD_ApolloField $RD_ApolloKey
  ${NSD_GetState} $RD_ApolloSkip $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $RD_ApolloKey ""
  ${EndIf}
FunctionEnd

; Replace the APOLLO_API_KEY line in $R0\.env, or start the .env from
; .env.example. Every other line is kept as it is. In: $R0 = account-agent folder.
Function RD_WriteApolloKey
  StrCpy $R1 "$R0\.env"
  IfFileExists $R1 +2
    StrCpy $R1 "$R0\.env.example"
  StrCpy $R5 0
  FileOpen $R3 "$R0\.env.rdnew" w
  IfErrors rd_ak_fail
  IfFileExists $R1 0 rd_ak_append
  FileOpen $R2 $R1 r
  rd_ak_loop:
    ClearErrors
    FileRead $R2 $R4
    IfErrors rd_ak_eof
    StrCpy $R6 $R4 15
    ${If} $R6 == "APOLLO_API_KEY="
      FileWrite $R3 "APOLLO_API_KEY=$RD_ApolloKey$\r$\n"
      StrCpy $R5 1
    ${Else}
      FileWrite $R3 $R4
    ${EndIf}
    Goto rd_ak_loop
  rd_ak_eof:
  FileClose $R2
  rd_ak_append:
  ${If} $R5 == 0
    FileWrite $R3 "$\r$\nAPOLLO_API_KEY=$RD_ApolloKey$\r$\n"
  ${EndIf}
  FileClose $R3
  System::Call 'kernel32::MoveFileExW(w "$R0\.env.rdnew", w "$R0\.env", i 1) i .r9'
  ${If} $9 == 0
    Delete "$R0\.env.rdnew"
    DetailPrint "Could not save the Apollo key. Add it in Recruit Desk Settings, API keys."
  ${EndIf}
  Return
  rd_ak_fail:
  DetailPrint "Could not save the Apollo key. Add it in Recruit Desk Settings, API keys."
FunctionEnd

; Called by RD_InstallAgents after the assistants are copied.
Function RD_InstallApolloKey
  ${If} $RD_ApolloKey != ""
    StrCpy $R0 "$RD_AgentsDir\account-agent"
    Call RD_WriteApolloKey
    StrCpy $RD_ApolloKey ""
    DetailPrint "Saved the Apollo key for the account assistant."
  ${EndIf}
FunctionEnd
