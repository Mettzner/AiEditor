"use client";

import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export interface Option {
  value: string;
  label: string;
}

export function SimpleSelect({
  value,
  onChange,
  options,
  placeholder = "Selecione…",
  className,
  disabled,
}: {
  value: string | null;
  onChange: (v: string) => void;
  options: Option[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <Select
      value={value}
      onValueChange={(v) => v != null && onChange(v as string)}
      items={options}
      disabled={disabled}
    >
      <SelectTrigger className={cn("w-full", className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Slider entre dois polos (ex.: Real ↔ IA). `value` é a % do polo da esquerda. */
export function SplitSlider({
  left,
  right,
  value,
  onChange,
  disabled,
}: {
  left: string;
  right: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between text-sm">
        <span>
          {left} <b className="tabular-nums">{value}%</b>
        </span>
        <span>
          <b className="tabular-nums">{100 - value}%</b> {right}
        </span>
      </div>
      <Slider
        value={[value]}
        onValueChange={(v) => onChange(Array.isArray(v) ? v[0] : v)}
        min={0}
        max={100}
        step={5}
        disabled={disabled}
      />
    </div>
  );
}
