"""SYSU validation calibration, frozen inference and per-pool aggregation."""
from exp8_bootstrap import bootstrap, evaluation_module, mode_from_cli


if __name__ == '__main__':
    bootstrap(mode_from_cli())
    evaluation_module().main()
