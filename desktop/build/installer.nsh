; Hooks into electron-builder's installer. After the app's files are in
; place, it runs the bundled Setup.exe (already elevated, so no second
; Windows prompt) to install or repair the services under C:\CaseFiles,
; and puts the local CA certificate next to the app so the app trusts it.
; The uninstaller removes the services but keeps case data, the audit log
; and certificates, as uninstall.cmd did.

!macro customInstall
  DetailPrint "Setting up the Case File Manager services. This takes about a minute."
  Delete "$TEMP\CaseFileManager-setup-error.txt"
  nsExec::ExecToLog '"$INSTDIR\resources\casefiles\Setup.exe" setup --desktop-app --ca-out "$INSTDIR\resources\local-ca.crt" --handoff "$TEMP\CaseFileManager-setup.txt" --error-out "$TEMP\CaseFileManager-setup-error.txt"'
  Pop $0
  ${If} $0 != 0
    StrCpy $1 "Setup.exe exited with code $0."
    ClearErrors
    FileOpen $2 "$TEMP\CaseFileManager-setup-error.txt" r
    ${IfNot} ${Errors}
      StrCpy $1 ""
      read_error_line:
        FileRead $2 $3
        ${IfNot} ${Errors}
          StrCpy $1 "$1$3"
          Goto read_error_line
        ${EndIf}
      FileClose $2
    ${EndIf}
    Delete "$TEMP\CaseFileManager-setup-error.txt"
    MessageBox MB_OK|MB_ICONSTOP "Case File Manager couldn't finish setting up.$\r$\n$\r$\n$1$\r$\n$\r$\nRun the installer again after fixing this. Your cases are kept." /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
!macroend

!macro customUnInstall
  ; An upgrade runs the old uninstaller first; the services stay.
  ${ifNot} ${isUpdated}
    DetailPrint "Removing the Case File Manager services. Case data is kept."
    ${If} ${FileExists} "C:\CaseFiles\launcher.exe"
      nsExec::ExecToLog '"C:\CaseFiles\launcher.exe" uninstall'
      Pop $0
    ${EndIf}
  ${endIf}
!macroend
