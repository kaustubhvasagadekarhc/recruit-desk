// Author: kaustubh.s.v
//! Read-only workbook access (calamine). The app never writes the agents' workbooks.

use calamine::{open_workbook_auto, Data, Reader};
use serde::Serialize;
use std::path::Path;

#[derive(Debug, Serialize)]
pub struct SheetData {
    pub sheets: Vec<String>,
    pub sheet: Option<String>,
    pub headers: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

fn cell_text(c: &Data) -> String {
    match c {
        Data::Empty => String::new(),
        Data::String(s) => s.clone(),
        Data::Float(f) if f.fract() == 0.0 && f.abs() < 1e15 => format!("{}", *f as i64),
        Data::Float(f) => format!("{f}"),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        Data::DateTime(d) => excel_serial_to_text(d.as_f64()),
        Data::DateTimeIso(s) | Data::DurationIso(s) => s.clone(),
        Data::Error(e) => format!("#{e:?}"),
    }
}

/// Excel serial date (days since 1899-12-30) to "YYYY-MM-DD" or "YYYY-MM-DD HH:MM".
fn excel_serial_to_text(serial: f64) -> String {
    let days = serial.floor() as i64 - 25569; // 1970-01-01
    let mins = ((serial - serial.floor()) * 1440.0).round() as i64;
    // Civil-from-days (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    if mins == 0 || mins == 1440 {
        format!("{y:04}-{m:02}-{d:02}")
    } else {
        format!("{y:04}-{m:02}-{d:02} {:02}:{:02}", mins / 60, mins % 60)
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn serial_dates() {
        assert_eq!(super::excel_serial_to_text(45_000.0), "2023-03-15");
        assert_eq!(super::excel_serial_to_text(45_000.5), "2023-03-15 12:00");
    }
}

/// Open `path` and return the sheet list plus the rows of `sheet` (the first sheet if None).
/// The first non-empty row is the header row.
pub fn read_sheet(path: &Path, sheet: Option<&str>) -> Result<SheetData, String> {
    if !path.exists() {
        return Ok(SheetData { sheets: vec![], sheet: None, headers: vec![], rows: vec![] });
    }
    let mut wb = open_workbook_auto(path).map_err(|e| format!("Cannot open {}: {e}", path.display()))?;
    let sheets = wb.sheet_names().to_vec();
    let name = match sheet {
        Some(s) if sheets.iter().any(|n| n == s) => s.to_string(),
        _ => match sheets.first() {
            Some(s) => s.clone(),
            None => return Ok(SheetData { sheets, sheet: None, headers: vec![], rows: vec![] }),
        },
    };
    let range = wb.worksheet_range(&name).map_err(|e| format!("Cannot read sheet {name}: {e}"))?;
    let mut rows = range
        .rows()
        .map(|r| r.iter().map(cell_text).collect::<Vec<_>>())
        .filter(|r| r.iter().any(|c| !c.trim().is_empty()));
    let headers = rows.next().unwrap_or_default();
    let rows = rows.collect();
    Ok(SheetData { sheets, sheet: Some(name), headers, rows })
}
