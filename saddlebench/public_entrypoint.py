"""Public CLI routing. No matching or scoring is implemented here."""
import sys

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args and args[0]=='score':
        # Preserve explicit historical local-reference scoring syntax.
        if any(a == '--reference' or a.startswith('--reference=') for a in args[1:]):
            from saddlebench.cli import main as legacy
            return legacy(args)
        # Preserve the previous provisional-score command when the user explicitly
        # requests its old --mode option. Default is the coverage/geometry board.
        if any(a == '--mode' or a.startswith('--mode=') for a in args[1:]):
            from saddlebench.score_modes import main as previous
            return previous(args[1:])
        from saddlebench.coverage_tiers import main as latest
        return latest(args[1:])
    if args and args[0]=='provisional-score':
        from saddlebench.score_modes import main as previous
        return previous(args[1:])
    if args and args[0]=='legacy-score':
        from saddlebench.cli import main as legacy
        return legacy(['score',*args[1:]])
    if args and args[0]=='render':
        from saddlebench.coverage_leaderboard import main as render
        return render(args[1:])
    if not args or args[0] in ('-h','--help'):
        print('usage: saddlebench {score|provisional-score|legacy-score|render} [options]')
        print('  score               DEFAULT: 0.20/0.10/0.05 A colored coverage/geometry leaderboard')
        print('  provisional-score   Previous HF provisional score and diagnostic counts')
        print('  legacy-score        Original local-reference extxyz scorer')
        print('  render              Render an existing coverage-tier JSON without rescoring')
        print('Run saddlebench score --help for prediction-submission options.')
        return 0 if args else 2
    print('Unknown command. Use saddlebench --help',file=sys.stderr)
    return 2
