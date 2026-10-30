export type ProgressReviewAnswerType = 'text' | 'rating' | 'yes-no' | 'select' | 'rag';

export interface ProgressReviewQuestion {
  id: string;
  label: string;
  type: ProgressReviewAnswerType;
  placeholder?: string;
  helpText?: string;
  options?: string[];
  required?: boolean;
  showWhen?: {
    questionId: string;
    value: string;
  };
}

export interface ProgressReviewSection {
  id: string;
  title: string;
  description: string;
  icon: string;
  questions: ProgressReviewQuestion[];
}

export type ProgressReviewResponses = Record<string, string>;

export const PROGRESS_REVIEW_SECTIONS: ProgressReviewSection[] = [
  {
    id: 'progress-checks',
    title: 'Progress Checks',
    description: 'Confirm attendance, workplace learning, KSB development and evidence quality.',
    icon: 'ri-check-double-line',
    questions: [
      { id: 'attendance_issues', label: 'Are there any issues with punctuality and attendance at teaching sessions or in the workplace?', type: 'yes-no' },
      { id: 'workplace_training_since_review', label: 'Has workplace training been undertaken since the last review?', type: 'yes-no' },
      { id: 'ksb_learning_activities', label: 'Have teaching and learning activities taken place to develop knowledge, skills and behaviours?', type: 'yes-no' },
      { id: 'evidence_timely', label: 'Is evidence being submitted on time and to an adequate standard?', type: 'yes-no' },
      { id: 'other_progress_issues', label: 'Are there any other issues, concerns, improvements or support needs?', type: 'yes-no' },
      {
        id: 'other_progress_issues_detail',
        label: 'Provide details of the issues, concerns or support needed.',
        type: 'text',
        placeholder: 'Describe the concern, agreed action, owner and review date...',
        required: false,
        showWhen: { questionId: 'other_progress_issues', value: 'Yes' },
      },
    ],
  },
  {
    id: 'learner-reflection',
    title: 'Learner Reflections & Ratings',
    description: 'Record the learner’s own 1–10 ratings and any additional comments.',
    icon: 'ri-user-heart-line',
    questions: [
      { id: 'learner_attitude_pride_rating', label: 'Your attitude and pride in your work and training', type: 'rating' },
      { id: 'learner_collaboration_rating', label: 'Your willingness to learn from and collaborate with colleagues', type: 'rating' },
      { id: 'learner_time_management_rating', label: 'Your ability to use time effectively, plan and prioritise tasks', type: 'rating' },
      { id: 'learner_respect_empathy_rating', label: 'Your demonstration of respect and empathy when dealing with others', type: 'rating' },
      { id: 'learner_english_confidence_rating', label: 'Your confidence in your English skills', type: 'rating' },
      { id: 'learner_maths_confidence_rating', label: 'Your confidence in your maths skills', type: 'rating' },
      { id: 'learner_wider_skills_rating', label: 'Your development of wider skills including digital skills, communication and problem solving', type: 'rating' },
      { id: 'learner_workplace_behaviours_rating', label: 'Your development of workplace attributes and behaviours, including attitude to learning and resilience', type: 'rating' },
      { id: 'learner_provider_safeguarding_confidence_rating', label: 'Your confidence that a safeguarding concern would be taken seriously by your training provider', type: 'rating' },
      { id: 'learner_employer_safeguarding_confidence_rating', label: 'Your confidence that a safeguarding concern would be taken seriously by your employer', type: 'rating' },
      { id: 'learner_provider_support_rating', label: 'Your rating of the support received from your training provider or tutor', type: 'rating' },
      { id: 'learner_manager_support_rating', label: 'Your rating of the support provided by your organisation or line manager', type: 'rating' },
      { id: 'learner_additional_comments', label: 'Do you have any additional comments?', type: 'text', placeholder: 'Add the learner’s comments...', required: false },
    ],
  },
  {
    id: 'manager-reflection',
    title: 'Manager Reflections & Ratings',
    description: 'Capture workplace application, behaviours and the line manager’s assessment.',
    icon: 'ri-briefcase-line',
    questions: [
      { id: 'manager_learning_application_rating', label: 'The learner’s application of learning in the workplace', type: 'rating' },
      { id: 'manager_wider_skills_rating', label: 'The development of wider skills including digital skills, communication and problem solving', type: 'rating' },
      { id: 'manager_workplace_behaviours_rating', label: 'The development of workplace attributes and behaviours, including attitude to learning and resilience', type: 'rating' },
      {
        id: 'manager_valued_application',
        label: 'What is one area where you have particularly valued the apprentice applying their learning at work over the last three months?',
        type: 'text',
        placeholder: 'Give a specific workplace example...',
      },
      {
        id: 'manager_progress_summary',
        label: 'Provide a short summary of how you feel the learner is progressing, to help tailor support to individual needs.',
        type: 'text',
        placeholder: 'Summarise progress, strengths and support priorities...',
      },
    ],
  },
  {
    id: 'tutor-reflection',
    title: 'Tutor Reflections & Ratings',
    description: 'Record the tutor’s professional assessment and agreed development focus.',
    icon: 'ri-user-star-line',
    questions: [
      { id: 'tutor_learning_attitude_rating', label: 'Positive attitude towards learning and working collaboratively', type: 'rating' },
      { id: 'tutor_time_management_rating', label: 'Ability to use time effectively, plan and prioritise', type: 'rating' },
      { id: 'tutor_respect_empathy_rating', label: 'Ability to demonstrate respect and empathy when dealing with others', type: 'rating' },
      { id: 'tutor_english_maths_rating', label: 'Development of English and maths skills', type: 'rating' },
      { id: 'tutor_workplace_behaviours_rating', label: 'Development of positive attributes and behaviours, including attitude to learning and resilience', type: 'rating' },
      {
        id: 'tutor_strengths',
        label: 'What is one area where the learner has achieved or performed well over the last three months?',
        type: 'text',
        placeholder: 'Record a specific strength or achievement...',
      },
      {
        id: 'tutor_progress_summary',
        label: 'Provide a short summary of how you feel the learner is progressing.',
        type: 'text',
        placeholder: 'Summarise overall progress and the next development priority...',
      },
    ],
  },
  {
    id: 'safeguarding',
    title: 'Safeguarding & Key Themes',
    description: 'Confirm safeguarding knowledge, concerns and the programme theme discussed.',
    icon: 'ri-shield-check-line',
    questions: [
      { id: 'safeguarding_understood', label: 'Do you have an understanding of Safeguarding?', type: 'yes-no' },
      { id: 'prevent_understood', label: 'Do you have an understanding of Prevent as part of Safeguarding?', type: 'yes-no' },
      { id: 'safeguarding_reporting_understood', label: 'Do you know how to report a Safeguarding concern?', type: 'yes-no' },
      { id: 'safeguarding_reporting_process', label: 'How would you report a Safeguarding concern, and who would you report it to?', type: 'text', placeholder: 'Record the reporting route and named contact...' },
      { id: 'safeguarding_concerns', label: 'Are there any Safeguarding concerns to be raised?', type: 'yes-no' },
      {
        id: 'safeguarding_concerns_detail',
        label: 'Provide details of the safeguarding concern and immediate action taken.',
        type: 'text',
        placeholder: 'Record only the information required for escalation...',
        required: false,
        showWhen: { questionId: 'safeguarding_concerns', value: 'Yes' },
      },
      {
        id: 'key_theme',
        label: 'Select one of the themes you have engaged with as part of your programme since the last review.',
        type: 'select',
        options: ['Health & Safety', 'Prevent', 'British Values', 'Equality, Diversity & Inclusion', 'Online Safety', 'Wellbeing', 'Other'],
      },
      {
        id: 'key_theme_other',
        label: 'If other, please provide details.',
        type: 'text',
        placeholder: 'Enter the theme...',
        required: false,
        showWhen: { questionId: 'key_theme', value: 'Other' },
      },
      { id: 'key_theme_comments', label: 'Please add your comments relating to the selected theme.', type: 'text', placeholder: 'Explain how the theme was discussed or applied...' },
    ],
  },
  {
    id: 'additional-support',
    title: 'Additional Support',
    description: 'Identify learning support, adjustments and any other circumstances requiring action.',
    icon: 'ri-hand-heart-line',
    questions: [
      { id: 'additional_learning_support', label: 'Do you have any additional learning support needs?', type: 'yes-no' },
      {
        id: 'additional_learning_support_detail',
        label: 'Describe the additional learning support required.',
        type: 'text',
        placeholder: 'Describe the need, agreed adjustment and owner...',
        required: false,
        showWhen: { questionId: 'additional_learning_support', value: 'Yes' },
      },
      { id: 'health_adjustments', label: 'Do you have any health conditions or require any adjustments?', type: 'yes-no' },
      {
        id: 'health_adjustments_detail',
        label: 'Describe the condition or adjustment required.',
        type: 'text',
        placeholder: 'Record the adjustment and follow-up action...',
        required: false,
        showWhen: { questionId: 'health_adjustments', value: 'Yes' },
      },
      { id: 'other_support_circumstances', label: 'Are there any other circumstances that we could support with?', type: 'yes-no' },
      {
        id: 'other_support_detail',
        label: 'Provide details of the other support required.',
        type: 'text',
        placeholder: 'Describe the circumstances and agreed support...',
        required: false,
        showWhen: { questionId: 'other_support_circumstances', value: 'Yes' },
      },
    ],
  },
  {
    id: 'actions',
    title: 'Progress Targets & Actions',
    description: 'Review previous targets and record clear actions for the next period.',
    icon: 'ri-focus-3-line',
    questions: [
      { id: 'previous_targets_achieved', label: 'Have progress targets and actions from the previous review been achieved?', type: 'yes-no' },
      {
        id: 'previous_targets_detail',
        label: 'Explain which previous targets remain outstanding.',
        type: 'text',
        placeholder: 'List outstanding actions and the reason for delay...',
        required: false,
        showWhen: { questionId: 'previous_targets_achieved', value: 'No' },
      },
      { id: 'targets_actions', label: 'Agreed progress targets and actions from this review', type: 'text', placeholder: 'Set specific, measurable targets for the next review period...' },
      { id: 'action_owners_dates', label: 'Confirm the owner and due date for each agreed action.', type: 'text', placeholder: 'Example: Learner — upload evidence — 30 September...' },
    ],
  },
  {
    id: 'rag',
    title: 'RAG Status',
    description: 'Set the overall review status and explain the evidence supporting it.',
    icon: 'ri-traffic-light-line',
    questions: [
      { id: 'rag_status', label: 'Select the learner’s current RAG status.', type: 'rag' },
      { id: 'rag_reason', label: 'Explain the reason for this RAG status.', type: 'text', placeholder: 'Summarise the evidence behind the selected status...' },
    ],
  },
];

export const REQUIRED_PROGRESS_REVIEW_RESPONSE_IDS = PROGRESS_REVIEW_SECTIONS.flatMap(
  (section) => section.questions
    .filter((question) => question.required !== false)
    .map((question) => question.id),
);

export function responsesForSection(
  responses: ProgressReviewResponses | undefined,
  sectionId: string,
) {
  const section = PROGRESS_REVIEW_SECTIONS.find((item) => item.id === sectionId);
  if (!section || !responses) return [];
  return section.questions
    .map((question) => ({ ...question, answer: responses[question.id]?.trim() || '' }))
    .filter((item) => item.answer);
}
