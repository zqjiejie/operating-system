from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from simple_fs import FileSystemError, Node, VirtualFileSystem, fmt_time


class FileEditor(tk.Toplevel):
    def __init__(self, app: "FileManagerApp", node: Node):
        super().__init__(app.root)
        self.app = app
        self.node = node
        self.title(f"编辑文件 - {node.name}")
        self.geometry("620x420")
        self.minsize(420, 260)
        self.transient(app.root)

        self.text = tk.Text(self, wrap="word", undo=True, font=("Microsoft YaHei UI", 10))
        self.text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 6))
        self.text.insert("1.0", app.read_node_text(node))
        self.saved_text = self.text.get("1.0", "end-1c")

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(bar, text="保存", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(bar, text="关闭", command=self.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-s>", self.save)
        self.text.focus_set()

    def save(self, _event: tk.Event | None = None) -> None:
        try:
            content = self.text.get("1.0", "end-1c")
            self.app.write_node_text(self.node, content)
            self.saved_text = content
            self.app.refresh_all()
            messagebox.showinfo("保存成功", "文件内容已经写入虚拟磁盘。", parent=self)
        except FileSystemError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)

    def close(self) -> None:
        content = self.text.get("1.0", "end-1c")
        if content != self.saved_text:
            choice = messagebox.askyesnocancel("保存文件", "文件内容已修改，是否保存？", parent=self)
            if choice is None:
                return
            if choice:
                self.save()
        self.destroy()


class FileManagerApp:
    def __init__(self) -> None:
        self.fs = VirtualFileSystem()
        self.fs.load()

        self.root = tk.Tk()
        self.root.title("文件管理系统")
        self.root.geometry("980x620")
        self.root.minsize(760, 460)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.selected_node: Node | None = None
        self.tree_items: dict[str, Node] = {}
        self.list_items: dict[str, Node] = {}

        self.make_style()
        self.build_ui()
        self.refresh_all()

    def make_style(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=26)
        style.configure("Path.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", foreground="#475569")

    def build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill=tk.X)

        ttk.Button(top, text="格式化", command=self.format_disk).pack(side=tk.LEFT)
        ttk.Button(top, text="上级", command=self.go_parent).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="新建文件夹", command=self.create_dir).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="新建文件", command=self.create_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="刷新", command=self.refresh_all).pack(side=tk.LEFT, padx=(8, 0))

        self.path_var = tk.StringVar()
        ttk.Label(top, textvariable=self.path_var, style="Path.TLabel").pack(side=tk.LEFT, padx=(18, 0))

        pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=3)

        ttk.Label(left, text="目录结构").pack(anchor=tk.W, pady=(0, 4))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.show_tree_menu)

        ttk.Label(right, text="文件详情").pack(anchor=tk.W, pady=(0, 4))
        columns = ("name", "type", "size", "first", "modified")
        self.listbox = ttk.Treeview(right, columns=columns, show="headings", selectmode="browse")
        self.listbox.heading("name", text="文件名")
        self.listbox.heading("type", text="文件类型")
        self.listbox.heading("size", text="文件大小")
        self.listbox.heading("first", text="物理地址")
        self.listbox.heading("modified", text="修改日期")
        self.listbox.column("name", width=220, anchor=tk.W)
        self.listbox.column("type", width=90, anchor=tk.CENTER)
        self.listbox.column("size", width=90, anchor=tk.E)
        self.listbox.column("first", width=90, anchor=tk.CENTER)
        self.listbox.column("modified", width=170, anchor=tk.CENTER)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<Double-1>", self.open_selected)
        self.listbox.bind("<Button-3>", self.show_list_menu)

        status = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        status.pack(fill=tk.X)
        self.status_var = tk.StringVar()
        self.bitmap_var = tk.StringVar()
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.bitmap_var, style="Status.TLabel").pack(side=tk.RIGHT)

        self.blank_menu = tk.Menu(self.root, tearoff=False)
        self.blank_menu.add_command(label="新建文件夹", command=self.create_dir)
        self.blank_menu.add_command(label="新建文件", command=self.create_file)
        self.blank_menu.add_separator()
        self.blank_menu.add_command(label="返回上级", command=self.go_parent)
        self.blank_menu.add_command(label="格式化", command=self.format_disk)
        self.blank_menu.add_command(label="刷新", command=self.refresh_all)

        self.item_menu = tk.Menu(self.root, tearoff=False)
        self.item_menu.add_command(label="打开", command=self.open_selected)
        self.item_menu.add_command(label="重命名", command=self.rename_selected)
        self.item_menu.add_command(label="删除", command=self.delete_selected)
        self.item_menu.add_command(label="属性", command=self.show_attributes)

    def run(self) -> None:
        self.root.mainloop()

    def refresh_all(self) -> None:
        self.refresh_tree()
        self.refresh_list()
        self.path_var.set(f"当前路径：{self.fs.path_of()}")
        used = self.fs.block_count - self.fs.free_blocks()
        self.bitmap_var.set(f"空间：已用 {used} / 总计 {self.fs.block_count} 块")

    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.tree_items.clear()
        root_id = self.tree.insert("", tk.END, text="/", open=True)
        self.tree_items[root_id] = self.fs.root
        self.add_tree_children(root_id, self.fs.root)
        self.select_tree_node(self.fs.cwd)

    def add_tree_children(self, parent_id: str, node: Node) -> None:
        for child in sorted(node.children.values(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            item_id = self.tree.insert(parent_id, tk.END, text=child.name, open=True)
            self.tree_items[item_id] = child
            self.add_tree_children(item_id, child)

    def select_tree_node(self, target: Node) -> None:
        for item_id, node in self.tree_items.items():
            if node is target:
                self.tree.selection_set(item_id)
                self.tree.see(item_id)
                break

    def refresh_list(self) -> None:
        self.listbox.delete(*self.listbox.get_children())
        self.list_items.clear()
        for node in self.fs.list_dir():
            node_type = "文件夹" if node.is_dir() else "文件"
            size = "-" if node.is_dir() else f"{node.length} B"
            first = "-" if node.first_block < 0 else str(node.first_block)
            item_id = self.listbox.insert(
                "",
                tk.END,
                values=(node.name, node_type, size, first, fmt_time(node.modified_at)),
            )
            self.list_items[item_id] = node
        count = len(self.list_items)
        self.status_var.set(f"当前文件夹中共有 {count} 个项目")

    def on_tree_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        node = self.tree_items.get(selection[0])
        if node and node.is_dir():
            self.fs.cwd = node
            self.refresh_list()
            self.path_var.set(f"当前路径：{self.fs.path_of()}")

    def on_tree_double_click(self, _event: tk.Event) -> None:
        self.on_tree_select(_event)

    def selected_from_list(self) -> Node | None:
        selection = self.listbox.selection()
        self.selected_node = self.list_items.get(selection[0]) if selection else None
        return self.selected_node

    def show_list_menu(self, event: tk.Event) -> None:
        item = self.listbox.identify_row(event.y)
        if item:
            self.listbox.selection_set(item)
            self.selected_node = self.list_items.get(item)
            self.item_menu.tk_popup(event.x_root, event.y_root)
        else:
            self.selected_node = None
            self.blank_menu.tk_popup(event.x_root, event.y_root)

    def show_tree_menu(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            node = self.tree_items.get(item)
            if node:
                self.fs.cwd = node
                self.selected_node = node
                self.refresh_list()
                self.item_menu.tk_popup(event.x_root, event.y_root)
        else:
            self.blank_menu.tk_popup(event.x_root, event.y_root)

    def open_selected(self, _event: tk.Event | None = None) -> None:
        node = self.selected_node or self.selected_from_list()
        if not node:
            return
        if node.is_dir():
            self.fs.cwd = node
            self.refresh_all()
        else:
            FileEditor(self, node)

    def create_dir(self) -> None:
        name = simpledialog.askstring("新建文件夹", "请输入文件夹名称：", parent=self.root)
        if name:
            self.safe_action(lambda: self.fs.mkdir(name.strip()))

    def create_file(self) -> None:
        name = simpledialog.askstring("新建文件", "请输入文件名称：", parent=self.root)
        if name:
            self.safe_action(lambda: self.fs.create(name.strip()))

    def rename_selected(self) -> None:
        node = self.selected_node or self.selected_from_list()
        if not node or node is self.fs.root:
            return
        new_name = simpledialog.askstring("重命名", "请输入新的名称：", initialvalue=node.name, parent=self.root)
        if new_name:
            self.safe_action(lambda: self.fs.rename(self.fs.path_of(node), new_name.strip()))

    def delete_selected(self) -> None:
        node = self.selected_node or self.selected_from_list()
        if not node or node is self.fs.root:
            return
        if not messagebox.askyesno("确认删除", f"确定删除“{node.name}”吗？", parent=self.root):
            return
        if node.is_dir():
            if node is self.fs.cwd:
                self.fs.cwd = node.parent or self.fs.root
            self.safe_action(lambda: self.fs.rmdir(self.fs.path_of(node)))
        else:
            self.safe_action(lambda: self.fs.delete_file(self.fs.path_of(node)))

    def show_attributes(self) -> None:
        node = self.selected_node or self.selected_from_list()
        if not node:
            return
        info = (
            f"路径：{self.fs.path_of(node)}\n"
            f"类型：{'文件夹' if node.is_dir() else '文件'}\n"
            f"大小：{node.length} B\n"
            f"物理地址：{'-' if node.first_block < 0 else node.first_block}\n"
            f"创建时间：{fmt_time(node.created_at)}\n"
            f"修改时间：{fmt_time(node.modified_at)}"
        )
        messagebox.showinfo("文件属性", info, parent=self.root)

    def go_parent(self) -> None:
        self.fs.cwd = self.fs.cwd.parent or self.fs.root
        self.refresh_all()

    def format_disk(self) -> None:
        if not messagebox.askyesno("格式化", "格式化会清空虚拟磁盘中的所有目录和文件，是否继续？", parent=self.root):
            return
        blocks = simpledialog.askinteger("格式化", "块数量：", initialvalue=128, minvalue=1, parent=self.root)
        if not blocks:
            return
        block_size = simpledialog.askinteger("格式化", "每块大小（字节）：", initialvalue=64, minvalue=1, parent=self.root)
        if not block_size:
            return
        self.safe_action(lambda: self.fs.format(blocks, block_size))

    def read_node_text(self, node: Node) -> str:
        fd = self.fs.open(self.fs.path_of(node), "r")
        try:
            return self.fs.read(fd)
        finally:
            self.fs.close(fd)

    def write_node_text(self, node: Node, text: str) -> None:
        fd = self.fs.open(self.fs.path_of(node), "w")
        try:
            self.fs.write(fd, text)
        finally:
            self.fs.close(fd)
        self.fs.save()

    def safe_action(self, action) -> None:
        try:
            action()
            self.fs.save()
            self.refresh_all()
        except FileSystemError as exc:
            messagebox.showerror("操作失败", str(exc), parent=self.root)

    def on_close(self) -> None:
        try:
            self.fs.save()
        finally:
            self.root.destroy()


if __name__ == "__main__":
    FileManagerApp().run()
