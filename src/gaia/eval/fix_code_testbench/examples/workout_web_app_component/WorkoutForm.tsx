// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import type { Workout } from "@prisma/client";

interface WorkoutFormProps {
  initialData?: Partial<Workout>;
  mode?: "create" | "edit";
}

export function WorkoutForm({ initialData, mode = "create" }: WorkoutFormProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formData, setFormData] = useState({
    name: "",
    duration: 0,
    date: "",
    goal: ""
  });
  const dateFields = ["date"];

  const normalizePayload = (data: typeof formData) => {
    if (dateFields.length === 0) {
      return data;
    }

    const normalized = { ...data };

    dateFields.forEach((field) => {
      const value = normalized[field as keyof typeof normalized];
      if (!value) {
        return;
      }

      const parsedValue = new Date(value as string | number | Date);
      if (!Number.isNaN(parsedValue.getTime())) {
        normalized[field as keyof typeof normalized] = parsedValue.toISOString();
      }
    });

    return normalized;
  };

  // Initialize form with initialData when in edit mode
  useEffect(() => {
    if (initialData && mode === "edit") {
      setFormData(prev => ({
        ...prev,
        ...Object.fromEntries(
          Object.entries(initialData).filter(([key]) =>
            !["id", "createdAt", "updatedAt"].includes(key)
          )
        )
      }));
    }
  }, [initialData, mode]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    const checked = (e.target as HTMLInputElement).checked;

    setFormData(prev => ({
      ...prev,
      [name]: type === "checkbox" ? checked : type === "number" ? parseFloat(value) : value
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const url = mode === "create"
        ? "/api/workouts"
        : `/api/workouts/${initialData?.id}`;

      const method = mode === "create" ? "POST" : "PATCH";
      const payload = normalizePayload(formData);

      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "Operation failed");
      }

      router.push("/workouts");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="form-group">
        <label className="label-text block mb-2">Workout Name</label>
        <input
          type="text"
          name="name"
          value={formData.name}
          onChange={handleChange}
          className="input-field w-full"
          required
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="form-group">
          <label className="label-text block mb-2">Duration (minutes)</label>
          <input
            type="number"
            name="duration"
            value={formData.duration}
            onChange={handleChange}
            className="input-field w-full"
            required
            min="1"
          />
        </div>

        <div className="form-group">
          <label className="label-text block mb-2">Date</label>
          <input
            type="date"
            name="date"
            value={formData.date}
            onChange={handleChange}
            className="input-field w-full"
            required
          />
        </div>
      </div>

      <div className="form-group">
        <label className="label-text block mb-2">Goal</label>
        <input
          type="text"
          name="goal"
          value={formData.goal}
          onChange={handleChange}
          className="input-field w-full"
          required
        />
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl">
          {error}
        </div>
      )}

      <div className="flex gap-4 pt-4">
        <button
          type="submit"
          disabled={loading}
          className="flex-1 btn-primary"
        >
          {loading ? "Saving..." : mode === "create" ? "Create Workout" : "Save Changes"}
        </button>
        <button
          type="button"
          onClick={() => router.back()}
          className="btn-secondary px-6 py-3"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}