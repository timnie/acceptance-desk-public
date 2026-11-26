"""PyInstaller 冻结程序须先处理 multiprocessing 的子进程入口。"""
if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    from desktop.launcher import main

    main()
