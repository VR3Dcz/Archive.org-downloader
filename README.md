# Archive.org Robust Downloader – User Manual

Welcome to the **Archive.org Robust Downloader**. This application provides a powerful, graphical, and thread-safe way to download collections and individual files from Archive.org. It is designed with enterprise-grade architecture to ensure your downloads are fast, resumable, and cryptographically verified.

<img width="902" height="832" alt="screenshot" src="https://github.com/user-attachments/assets/bfbf95c3-263a-4f82-83dd-03fb8045d2b3" />

## Table of Contents
1. [Getting Started](#getting-started)
2. [Interface Overview](#interface-overview)
3. [How to Download Files](#how-to-download-files)
4. [Advanced Features & Settings](#advanced-features--settings)
5. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Using the Pre-compiled Version (Windows / macOS / Linux)
If you downloaded a release archive (`.zip` or `.tar.gz`) from GitHub:
1. Extract the archive to a folder on your computer.
2. Run the `ArchiveDownloader` executable file. No installation or Python setup is required.

### Running from Source Code
If you are running the application directly from the source code:
1. Ensure you have **Python 3.8+** installed.
2. Run the setup script for your OS (`install.bat` for Windows, `./install.sh` for Linux/macOS) to create a virtual environment and install dependencies.
3. Use the run script (`run.bat` or `./run.sh`) to launch the graphical interface.

---

## Interface Overview

The application is divided into several logical sections:

* **URL Input Box:** The main text area where you paste Archive.org links.
* **Settings Panel:** Contains controls for your output folder, file filters, concurrency, and speed limits.
* **Control Buttons:** Start or globally cancel the download processes.
* **Overall Progress:** Displays the total number of files processed versus the total discovered.
* **Active Downloads:** A scrollable list showing real-time progress, speed, ETA, and individual controls for currently downloading files.
* **Activity Log:** A console at the bottom providing detailed system events, warnings, and success messages.

---

## How to Download Files

1. **Add URLs:**
   Paste your Archive.org links into the top text box. You can add one URL per line. The application supports:
   * **Item URLs:** (e.g., `https://archive.org/details/example-item`)
   * **Search URLs:** Paste an advanced search URL to download all items matching a query.

2. **Select Output Folder:**
   Click **Browse...** to choose where the downloaded files should be saved. The application will automatically create sub-folders for each Archive.org item inside this directory.

3. **Set Filters (Optional):**
   If you only want specific file types, enter their extensions separated by commas in the **Allowed Extensions** box (e.g., `.mp3, .pdf, .zip`). Leave this field empty to download everything available in the item.

4. **Start Downloading:**
   Click **Start / Queue Downloads**. The application will first discover all metadata and then begin downloading files according to your settings.

---

## Advanced Features & Settings

This application is built with robust backend mechanisms to handle unstable networks and massive archives.

### Parallel Downloads (Concurrency)
You can choose how many files to download simultaneously (from 1 to 20). 
* *Recommendation:* 3 to 5 concurrent downloads is usually optimal. Setting this too high might cause Archive.org to temporarily throttle your connection.

### Global Speed Limit (Rate Limiting)
If you want to use the internet while downloading massive archives, you can throttle the application's bandwidth.
* Enter a number in the **Speed Limit** box and select the unit (`KB/s` or `MB/s`). 
* Entering `0` removes all limits (Max Speed).
* This limit is applied *globally* across all active downloads using a strict Token Bucket algorithm. You can change this value on the fly without pausing your downloads.

### Individual Task Control
In the **Active Downloads** section, you can click **Cancel** next to any specific file. If a file gets stuck or is canceled, the button changes to **Restart**, allowing you to re-queue the file instantly.

### Smart Resume & Integrity Checks
* **Auto-Resume:** If you close the application or lose your internet connection, simply start the download again with the same URL and output folder. The application will automatically resume partially downloaded files exactly where it left off.
* **Cryptographic Verification:** Once a file is downloaded, the application compares its MD5 or SHA1 hash against Archive.org's database. If network corruption occurs, the corrupted file is silently discarded and redownloaded to ensure 100% data integrity.

---

## Troubleshooting

* **Downloads fail immediately:** Ensure you have write permissions for the selected "Download Folder" and that your hard drive is not full.
* **"Metadata fetch failed" in the log:** Archive.org might be temporarily down or rate-limiting your IP address. Wait a few minutes and try again.
* **Speeds are fluctuating rapidly:** Network speeds naturally fluctuate. The application uses an Exponential Moving Average (EMA) to smooth out the displayed speed and ETA, but extreme drops usually indicate routing issues between your ISP and Archive.org servers.
* **A file keeps failing checksum validation:** This usually means the file on the Archive.org server was updated while you were downloading it. Restarting the specific task will resolve it.


---


## ⚠️ Disclaimer

This tool is provided for educational and personal use. Please respect Archive.org's Terms of Service and bandwidth limitations. Be considerate with parallel downloads and download frequency.

Archive.org is a non-profit library offering free access to millions of items. Consider [donating](https://archive.org/donate/) to support their mission.
