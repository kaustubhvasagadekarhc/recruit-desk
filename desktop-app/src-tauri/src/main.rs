// Author: kaustubh.s.v
// Hide the console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    recruit_desk_lib::run()
}
