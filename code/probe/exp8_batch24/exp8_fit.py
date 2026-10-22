"""SYSU V1 full/clean and newly trained U0 none; delegate optimization to batch22."""
from exp8_bootstrap import bootstrap, load, mode_from_cli


if __name__ == '__main__':
    bootstrap(mode_from_cli(training=True))
    load('exp8_fit', '_batch24_fit').main()
