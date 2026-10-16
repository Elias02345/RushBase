import * as RadixCheckbox from "@radix-ui/react-checkbox";
import * as RadixSelect from "@radix-ui/react-select";
import * as RadixSwitch from "@radix-ui/react-switch";
import { Check, ChevronDown, Search, X } from "lucide-react";
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";

import { cx } from "./cx";
import { Kbd } from "./Kbd";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

/** Text input with a soft rounded border and a clear focus ring. */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cx("rb-input", className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  mono?: boolean;
}

/** Multi-line input; `mono` switches to the monospace face for code-like content. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, mono, className, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cx("rb-input rb-textarea", mono && "rb-input--mono", className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
});

export interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: ReactNode;
  /** Render the control, spreading the given props onto it. */
  children: (control: {
    id: string;
    "aria-describedby": string | undefined;
    invalid: boolean;
  }) => ReactNode;
  className?: string;
}

/** Label, hint and error message wired to one control through generated ids. */
export function Field({ label, hint, error, children, className }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ");
  return (
    <div className={cx("rb-field", className)}>
      <label htmlFor={id} className="rb-field__label">
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy || undefined, invalid: Boolean(error) })}
      {hint ? (
        <p id={hintId} className="rb-field__hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="rb-field__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface SearchFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "onChange"> {
  value: string;
  onValueChange: (value: string) => void;
  clearLabel: string;
  /** Shortcut hint shown while the field is empty (for example "/"). */
  shortcut?: string;
  variant?: "md" | "lg";
}

/** Search input with a leading icon, a clear button and an optional shortcut hint. */
export const SearchField = forwardRef<HTMLInputElement, SearchFieldProps>(function SearchField(
  { value, onValueChange, clearLabel, shortcut, variant = "md", className, ...rest },
  ref,
) {
  return (
    <div className={cx("rb-search", variant === "lg" && "rb-search--lg", className)}>
      <Search className="rb-search__icon" size={variant === "lg" ? 22 : 18} aria-hidden="true" />
      <input
        ref={ref}
        type="search"
        className="rb-search__input"
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        {...rest}
      />
      {value ? (
        <button
          type="button"
          className="rb-search__clear"
          aria-label={clearLabel}
          onClick={() => onValueChange("")}
        >
          <X size={16} aria-hidden="true" />
        </button>
      ) : shortcut ? (
        <Kbd aria-hidden="true">{shortcut}</Kbd>
      ) : null}
    </div>
  );
});

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  /** Accessible name when no visible label is associated. */
  "aria-label"?: string;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/** Dropdown built on Radix Select (keyboard, typeahead, screen-reader support included). */
export function Select({
  value,
  onValueChange,
  options,
  placeholder,
  disabled,
  className,
  id,
  "aria-label": ariaLabel,
}: SelectProps) {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RadixSelect.Trigger id={id} className={cx("rb-select", className)} aria-label={ariaLabel}>
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon>
          <ChevronDown size={16} aria-hidden="true" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="rb-popover" position="popper" sideOffset={6}>
          <RadixSelect.Viewport className="rb-popover__viewport">
            {options.map((option) => (
              <RadixSelect.Item key={option.value} value={option.value} className="rb-option">
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
                <RadixSelect.ItemIndicator className="rb-option__check">
                  <Check size={14} aria-hidden="true" />
                </RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}

export interface CheckboxProps {
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  /** Visible label; omit and pass `aria-label` for label-less checkboxes. */
  label?: ReactNode;
  "aria-label"?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
  tabIndex?: number;
}

/** Checkbox on Radix with an animated tick. */
export function Checkbox({
  checked,
  onCheckedChange,
  label,
  className,
  id,
  ...rest
}: CheckboxProps) {
  const generated = useId();
  const controlId = id ?? generated;
  const box = (
    <RadixCheckbox.Root
      id={controlId}
      className="rb-checkbox"
      checked={checked}
      onCheckedChange={(next) => onCheckedChange(next === true)}
      {...rest}
    >
      <RadixCheckbox.Indicator className="rb-checkbox__indicator">
        <Check size={13} strokeWidth={3} aria-hidden="true" />
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  );
  if (!label) return box;
  return (
    <span className={cx("rb-check", className)}>
      {box}
      <label htmlFor={controlId}>{label}</label>
    </span>
  );
}

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
  className?: string;
}

/** Toggle switch with its label to the right. */
export function Switch({ checked, onCheckedChange, label, disabled, className }: SwitchProps) {
  const id = useId();
  return (
    <span className={cx("rb-check", className)}>
      <RadixSwitch.Root
        id={id}
        className="rb-switch"
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      >
        <RadixSwitch.Thumb className="rb-switch__thumb" />
      </RadixSwitch.Root>
      <label htmlFor={id}>{label}</label>
    </span>
  );
}
