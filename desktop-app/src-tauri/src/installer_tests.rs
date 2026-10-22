// Author: kaustubh.s.v
//! Static checks on the Windows installer files in windows/: the prerequisite
//! pages sit between Welcome and the directory and install pages, in the
//! agreed order, and the PowerShell helper stays ASCII (Windows PowerShell 5.1
//! reads BOM-less files as ANSI). The installer itself is built by makensis.

const TEMPLATE: &str = include_str!("../windows/installer.nsi");
const PAGES: &str = include_str!("../windows/prereq-pages.nsh");
const HOOKS: &str = include_str!("../windows/hooks.nsh");
const HELPER: &str = include_str!("../windows/rd-prereqs.ps1");
const CONF: &str = include_str!("../tauri.conf.json");
const EXA: &str = include_str!("../windows/exa-page.nsh");
const APOLLO: &str = include_str!("../windows/apollo-page.nsh");
const AGENTS_PS: &str = include_str!("../windows/rd-agents.ps1");

fn pos(hay: &str, needle: &str) -> usize {
    hay.find(needle).unwrap_or_else(|| panic!("missing: {needle}"))
}

#[test]
fn prereq_pages_come_after_welcome_and_before_install() {
    let welcome = pos(TEMPLATE, "!insertmacro MUI_PAGE_WELCOME");
    let prereqs = pos(TEMPLATE, "!include \"${RD_PREREQ_PAGES}\"");
    let reinstall = pos(TEMPLATE, "Page custom PageReinstall");
    let directory = pos(TEMPLATE, "!insertmacro MUI_PAGE_DIRECTORY");
    let instfiles = pos(TEMPLATE, "!insertmacro MUI_PAGE_INSTFILES");
    assert!(welcome < prereqs && prereqs < reinstall && reinstall < directory && directory < instfiles);
    assert!(HOOKS.contains("RD_PREREQ_PAGES") && HOOKS.contains("RD_PREREQ_SCRIPT"));
}

#[test]
fn prereq_pages_are_in_order_and_gate_next() {
    let order: Vec<usize> = ["RD_PREREQ_PAGE node", "RD_PREREQ_PAGE claude", "RD_PREREQ_PAGE login", "RD_PREREQ_PAGE uv"]
        .iter()
        .map(|n| pos(PAGES, &format!("!insertmacro {n}")))
        .collect();
    assert!(order.windows(2).all(|w| w[0] < w[1]));
    // Leaving a page without a passed check is refused, and every check has a timeout.
    assert!(PAGES.contains("Page custom RD_Show_${ID} RD_LeavePage"));
    assert!(PAGES.contains("${If} $RD_Ok != 1") && PAGES.contains("nsExec::ExecToStack /TIMEOUT="));
}

#[test]
fn helper_is_ascii_and_covers_every_step() {
    assert!(HELPER.is_ascii());
    for step in ["'node'", "'claude'", "'login'", "'uv'"] {
        assert!(HELPER.contains(step), "{step}");
    }
    for cmd in [
        "OpenJS.NodeJS.LTS",
        "https://nodejs.org/dist/index.json",
        "@anthropic-ai/claude-code",
        "https://claude.ai/install.ps1",
        "'auth', 'status'",
        "https://astral.sh/uv/install.ps1",
        "GetEnvironmentVariable('Path', 'Machine')",
    ] {
        assert!(HELPER.contains(cmd), "{cmd}");
    }
}

#[test]
fn config_uses_custom_template_and_current_user_mode() {
    let conf: serde_json::Value = serde_json::from_str(CONF).unwrap();
    let nsis = &conf["bundle"]["windows"]["nsis"];
    assert_eq!(nsis["template"], "windows/installer.nsi");
    assert_eq!(nsis["installerHooks"], "windows/hooks.nsh");
    assert_eq!(nsis["installMode"], "currentUser");
    assert_eq!(conf["bundle"]["publisher"], "kaustubh.s.v");
}

#[test]
fn exa_page_comes_after_the_prereq_pages() {
    let prereqs = pos(TEMPLATE, "!include \"${RD_PREREQ_PAGES}\"");
    let exa = pos(TEMPLATE, "!include \"${RD_EXA_PAGE}\"");
    let reinstall = pos(TEMPLATE, "Page custom PageReinstall");
    assert!(prereqs < exa && exa < reinstall);
    assert!(HOOKS.contains("RD_EXA_PAGE") && HOOKS.contains("RD_AGENTS_BUNDLE"));
    assert!(EXA.contains("Page custom RD_ShowExa RD_LeaveExa") && EXA.contains("https://dashboard.exa.ai"));
    assert!(EXA.contains("Skip, add later"));
}

#[test]
fn apollo_page_follows_exa_and_writes_only_account_agent() {
    let exa = pos(TEMPLATE, "!include \"${RD_EXA_PAGE}\"");
    let apollo = pos(TEMPLATE, "!include \"${RD_APOLLO_PAGE}\"");
    let reinstall = pos(TEMPLATE, "Page custom PageReinstall");
    assert!(exa < apollo && apollo < reinstall);
    assert!(HOOKS.contains("RD_APOLLO_PAGE"));
    assert!(APOLLO.contains("Page custom RD_ShowApollo RD_LeaveApollo") && APOLLO.contains("Skip, add later"));
    assert!(APOLLO.contains("\"APOLLO_API_KEY=$RD_ApolloKey") && APOLLO.contains("$RD_AgentsDir\\account-agent"));
    assert!(!APOLLO.contains("candidate-agent") && !APOLLO.contains("company-agent") && !APOLLO.contains("EXA_API_KEY="));
    assert!(EXA.contains("Call RD_InstallApolloKey"));
    for line in APOLLO.lines() {
        let l = line.trim_start();
        if l.starts_with("DetailPrint") || l.starts_with("nsExec") || l.starts_with("Exec") || l.starts_with("MessageBox") {
            assert!(!l.contains("$RD_ApolloKey"), "key leaks in: {l}");
        }
    }
}

#[test]
fn exa_key_is_never_logged_or_on_a_command_line() {
    for line in EXA.lines().chain(HOOKS.lines()) {
        let l = line.trim_start();
        if l.starts_with("DetailPrint") || l.starts_with("nsExec") || l.starts_with("Exec") || l.starts_with("MessageBox") {
            assert!(!l.contains("$RD_ExaKey"), "key leaks in: {l}");
        }
    }
    // Only the EXA line changes; HASDATA and FIRECRAWL are never written.
    assert!(EXA.contains("\"EXA_API_KEY=$RD_ExaKey"));
    assert!(!EXA.contains("HASDATA_API_KEY") && !EXA.contains("FIRECRAWL_API_KEY="));
}

#[test]
fn agents_install_to_documents_and_user_data_is_never_removed() {
    assert!(EXA.contains("$PROFILE\\Documents\\Recruit Desk\\AI-AGENTS"));
    assert!(EXA.contains("File /r \"${RD_AGENTS_BUNDLE}/candidate-agent/*\""));
    assert!(EXA.contains("File /r \"${RD_AGENTS_BUNDLE}/company-agent/*\""));
    assert!(EXA.contains("File /r \"${RD_AGENTS_BUNDLE}/account-agent/*\""));
    assert!(EXA.contains("${ForEach} $R8 1 3 + 1"), "uv sync must cover all three assistants");
    assert!(EXA.contains("agents-root.txt"));
    for src in [EXA, HOOKS] {
        for line in src.lines() {
            let l = line.trim_start();
            if l.starts_with("RMDir") || l.starts_with("RmDir") || l.starts_with("Delete") {
                assert!(!l.contains("RD_AgentsDir") && !l.contains("/r"), "removes user data: {l}");
            }
        }
    }
    assert!(HOOKS.contains("NSIS_HOOK_POSTINSTALL") && HOOKS.contains("NSIS_HOOK_POSTUNINSTALL"));
    assert!(HOOKS.contains("left in place"));
    assert!(AGENTS_PS.is_ascii() && AGENTS_PS.contains("sync"));
}
