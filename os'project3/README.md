# Simple File System Simulator

Run GUI:

```powershell
python gui_fs.py
```

Run command-line version:

```powershell
python simple_fs.py
```

Command-line commands:

- `format [blocks] [block_size]`
- `mkdir <name>`
- `rmdir <name>`
- `ls [-l] [path]`
- `cd <path>`
- `pwd`
- `create <file>`
- `open <file> [r|w|rw|a]`
- `close <fd>`
- `write <fd> <text>`
- `read <fd>`
- `rm <file>`
- `stat <path>`
- `bitmap`
- `help`
- `exit`

The virtual disk is saved to `fs_image.json` when the shell exits.
