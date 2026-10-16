/** Rushbase mark: a rounded tile holding a progress arc around a play triangle. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg
      className="logo"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="32" height="32" rx="10" className="logo__tile" />
      <circle
        cx="16"
        cy="16"
        r="9"
        fill="none"
        className="logo__arc"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="40 17"
        transform="rotate(-70 16 16)"
      />
      <path
        d="M14 12.6v6.8a.6.6 0 0 0 .9.5l5.4-3.4a.6.6 0 0 0 0-1l-5.4-3.4a.6.6 0 0 0-.9.5Z"
        className="logo__play"
      />
    </svg>
  );
}
