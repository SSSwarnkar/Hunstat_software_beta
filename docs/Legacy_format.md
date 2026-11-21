<!-- docs/Legacy_format.md -->
# Legacy HunStat `.tsv` File Format Specification

This document specifies the tab-separated value (`.tsv`) file format produced by the legacy HunStat software and supported as a read-only input format by `hunstat/io_files.py` (`is_legacy_hunstat_tsv`, `load_legacy_tsv`, `convert_legacy_tsv_to_csv`, `convert_legacy_folder`).

## 1. Encoding, Delimiter, and Line Endings

- **Character encoding**: ASCII / UTF-8
- **Field delimiter**: Horizontal tab (`\t`, `0x09`)
- **Line endings**: Windows CRLF (`\r\n`, `0x0D 0x0A`); parser also accepts LF (`\n`)
- **Floating-point precision**: 4 decimal places (`%.4f`, e.g., `-0.4028\t-11.1182`)

## 2. File Structure Overview

Every legacy `.tsv` file consists of a metadata header block, a 1-based section index table, a blank separator line, a `Data` count line, a two-column header line, and `<N>` contiguous tab-separated data rows:

```text
Version	2
Header
Technique	<tech_id>
Sections	<M>
<start_1>	<end_1>
...
<start_M>	<end_M>

Data	<N>
<Col1_Header>	Current/uA
<x_1>	<y_1>
...
<x_N>	<y_N>