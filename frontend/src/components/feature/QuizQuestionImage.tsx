export function QuizQuestionImage({ imageUrl }: { imageUrl?: string | null }) {
  if (!imageUrl) return null;
  return (
    <img src={imageUrl} alt="Question diagram"
      className="my-3 block h-auto max-h-[32rem] max-w-full rounded-xl border border-foreground-200 bg-white object-contain" />
  );
}
