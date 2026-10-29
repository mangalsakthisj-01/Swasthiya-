import { GoogleGenAI, Type, Schema } from '@google/genai';
import { AICategorizationResponse } from '../types';

const aiResponseSchema: Schema = {
  type: Type.OBJECT,
  properties: {
    suggestedCategory: {
      type: Type.STRING,
      description: 'Standardized municipal code: garbage_accumulation | sewage_overflow | blocked_drainage | stagnant_water | water_supply_contamination | damaged_infrastructure | unsanitary_public_toilet',
    },
    summary: {
      type: Type.STRING,
      description: 'Objective 1-to-2 sentence field summary of the civic infrastructure issue',
    },
    extractedObservations: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: 'Concrete physical features observed in description and photo (e.g. black water discoloration, plastic blockage, stagnant puddle diameter)',
    },
    missingInformation: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: 'Crucial operational context missing for field dispatch (e.g. exact pipe diameter, whether water enters houses)',
    },
    suggestedUrgency: {
      type: Type.STRING,
      enum: ['ROUTINE', 'MODERATE', 'HIGH', 'URGENT'],
      description: 'Suggested municipal response schedule urgency',
    },
    confidence: {
      type: Type.NUMBER,
      description: 'Confidence score between 0.0 and 1.0',
    },
    explanation: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: 'Transparent reasoning points explaining why this classification was chosen',
    },
    limitations: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: 'Mandatory disclaimer statement that automated vision/text cannot confirm microbiological pathogens or water chemical potability',
    },
    requiresHumanReview: {
      type: Type.BOOLEAN,
      description: 'True if ambiguity exists or critical public institutions are implicated',
    },
  },
  required: [
    'suggestedCategory',
    'summary',
    'extractedObservations',
    'missingInformation',
    'suggestedUrgency',
    'confidence',
    'explanation',
    'limitations',
    'requiresHumanReview',
  ],
};

export async function analyzeCivicComplaintWithGemini(
  title: string,
  description: string,
  userSelectedCategory?: string,
  imageBase64?: string
): Promise<AICategorizationResponse> {
  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    return generateDeterministicFallback(title, description, userSelectedCategory);
  }

  try {
    const ai = new GoogleGenAI({
      apiKey,
      httpOptions: {
        headers: {
          'User-Agent': 'aistudio-build',
        },
      },
    });

    const systemInstruction = `You are the civic intelligence and triage engine for SwasthyaGrid (ஸ்வஸ்த்யாகிரிட்).
Analyze citizen-reported municipal and public health hazards (drainage, sanitation, water stagnation, waste accumulation).

SAFETY AND OPERATIONAL MANDATES:
1. STRICT MEDICAL BOUNDARY: Never diagnose clinical conditions, infectious diseases, or specific pathogens. Treat all issues strictly as municipal infrastructure and environmental sanitation hazards.
2. OPERATIONAL URGENCY: Mark as URGENT only if sewage is contaminating potable lines, blocking major arterial school/hospital access, or overflowing inside living spaces.
3. LIMITATIONS: Explicitly state in the limitations field that citizen descriptions and digital photographs cannot assess chemical toxicity, biological coliform counts, or subterranean pipe integrity.`;

    const promptText = `Evaluate this civic incident report:
Title: "${title}"
Description: "${description}"
User-selected category: "${userSelectedCategory || 'unspecified'}"
Return the analysis strictly adhering to the JSON schema.`;

    const parts: any[] = [{ text: promptText }];
    if (imageBase64) {
      // Strip base64 prefix if included
      const cleanBase64 = imageBase64.replace(/^data:image\/[a-zA-Z]+;base64,/, '');
      parts.push({
        inlineData: {
          mimeType: 'image/jpeg',
          data: cleanBase64,
        },
      });
    }

    const response = await ai.models.generateContent({
      model: 'gemini-3.8-flash',
      contents: { parts },
      config: {
        systemInstruction,
        responseMimeType: 'application/json',
        responseSchema: aiResponseSchema,
        temperature: 0.1,
      },
    });

    const text = response.text;
    if (!text) {
      throw new Error('Empty response payload received from Gemini model');
    }

    const parsed: AICategorizationResponse = JSON.parse(text);
    return parsed;
  } catch (error) {
    console.warn('[GeminiService] AI generation encountered issue, invoking deterministic fallback:', error);
    return generateDeterministicFallback(title, description, userSelectedCategory);
  }
}

function generateDeterministicFallback(
  title: string,
  description: string,
  userCategory?: string
): AICategorizationResponse {
  const combined = `${title} ${description}`.toLowerCase();
  let category = userCategory || 'garbage_accumulation';
  let urgency: AICategorizationResponse['suggestedUrgency'] = 'MODERATE';
  const observations: string[] = [];
  const missing: string[] = ['On-site physical inspection measurements.'];

  if (combined.includes('sewage') || combined.includes('gutter') || combined.includes('drain') || combined.includes('சாக்கடை')) {
    category = combined.includes('drain') ? 'blocked_drainage' : 'sewage_overflow';
    urgency = 'HIGH';
    observations.push('Keywords indicate liquid waste blockage or overflow along public path.');
  } else if (combined.includes('water') || combined.includes('mosquito') || combined.includes('stagnan') || combined.includes('கொசு')) {
    category = 'stagnant_water';
    urgency = 'HIGH';
    observations.push('Report highlights vector risk and standing water pool.');
  } else if (combined.includes('potable') || combined.includes('drinking') || combined.includes('smell') || combined.includes('குடிநீர்')) {
    category = 'water_supply_contamination';
    urgency = 'URGENT';
    observations.push('Potential crossover with domestic water distribution.');
  } else {
    observations.push('General solid waste accumulation or civic maintenance concern.');
  }

  if (combined.includes('hospital') || combined.includes('school') || combined.includes('urgent') || combined.includes('பள்ளி')) {
    urgency = 'URGENT';
  }

  return {
    suggestedCategory: category,
    summary: title ? title.slice(0, 110) : 'Civic sanitation hazard recorded by citizen',
    extractedObservations: observations.length > 0 ? observations : ['Civic maintenance report submitted by citizen.'],
    missingInformation: missing,
    suggestedUrgency: urgency,
    confidence: 0.82,
    explanation: [
      'Determined using rule-based municipal keyword triage heuristics (Gemini AI server fallback mode).',
      `Identified category: ${category} with recommended ${urgency} dispatch.`,
    ],
    limitations: [
      'Automated heuristics cannot verify water chemistry, coliform counts, or subterranean infrastructure integrity without physical laboratory sampling.',
    ],
    requiresHumanReview: urgency === 'URGENT' || urgency === 'HIGH',
  };
}
