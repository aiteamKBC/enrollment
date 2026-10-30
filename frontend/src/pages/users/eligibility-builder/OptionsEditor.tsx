import { AppIcon } from '@/components/feature/AppIcon';
import { inputClass } from '../components/ui';

/**
 * Edit a list of answers: one box per answer, remove, add. Used for the
 * built-in choice questions' lists and for custom dropdowns. Blank boxes are
 * dropped when the form is published.
 */
export function OptionsEditor({
  options,
  onChange,
  name,
  showInvalid = false,
}: {
  options: readonly string[];
  onChange: (next: string[]) => void;
  /** What the answers belong to, for screen readers ("Programme"). */
  name: string;
  /** Mark blank and repeated answers. */
  showInvalid?: boolean;
}) {
  const trimmed = options.map((o) => o.trim());
  const repeated = new Set(trimmed.filter((o, i) => o && trimmed.indexOf(o) !== i));
  const set = (index: number, value: string) => onChange(options.map((o, i) => (i === index ? value : o)));
  return (
    <div>
      <ol className="space-y-1.5">
        {options.map((option, i) => (
          <li key={i} className="flex items-center gap-2">
            <input
              className={`${inputClass} flex-1`}
              value={option}
              placeholder={`Answer ${i + 1}`}
              aria-label={`${name}: answer ${i + 1}`}
              aria-invalid={showInvalid && (!trimmed[i] || repeated.has(trimmed[i])) ? true : undefined}
              onChange={(e) => set(i, e.target.value)}
            />
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-foreground-400 hover:bg-background-100 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
              aria-label={`Remove ${option.trim() ? `“${option.trim()}”` : `answer ${i + 1}`} from ${name}`}
              disabled={options.length === 1}
              onClick={() => onChange(options.filter((_, k) => k !== i))}
            >
              <AppIcon className="ri-delete-bin-line" />
            </button>
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-primary-700 hover:bg-primary-50"
        onClick={() => onChange([...options, ''])}
      >
        <AppIcon className="ri-add-line" />Add answer
      </button>
    </div>
  );
}
