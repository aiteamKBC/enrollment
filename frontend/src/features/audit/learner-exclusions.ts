const EXCLUDED_APTEM_IDS = new Set([3687, 4147, 4576, 6450, 6943, 9115]);

const EXCLUDED_LEARNER_NAMES = new Set([
  "amber deacon",
  "celine ababio",
  "colleen stewart",
  "freya johnson",
  "jackson cyprian",
  "joanna furnival",
  "joseph bailey",
  "wemimo buwanhot",
]);

function normalizedName(value: unknown): string {
  return String(value ?? "").trim().toLocaleLowerCase().replace(/\s+/g, " ");
}

export function isExcludedLearner(aptemId: unknown, learnerName: unknown): boolean {
  const numericId = Number(aptemId);
  return (
    (Number.isInteger(numericId) && EXCLUDED_APTEM_IDS.has(numericId)) ||
    EXCLUDED_LEARNER_NAMES.has(normalizedName(learnerName))
  );
}
