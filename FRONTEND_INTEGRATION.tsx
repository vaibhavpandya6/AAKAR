/**
 * Frontend Integration Guide: BRD to WBS Flow
 *
 * This shows how to integrate the BRD-to-WBS pipeline into your frontend
 * at http://localhost:8080
 */

const API_BASE = 'http://localhost:8080/api/v1';

// ============================================================================
// 1. Submit BRD and Create Project
// ============================================================================

interface CreateProjectRequest {
  prompt: string; // Your BRD text goes here
}

interface ProjectResponse {
  id: string;
  status: string;
  prompt: string;
  created_at: string;
  updated_at: string;
}

async function createProjectFromBRD(brdText: string, authToken: string): Promise<ProjectResponse> {
  const response = await fetch(`${API_BASE}/projects/create`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
    },
    body: JSON.stringify({
      prompt: brdText, // Your BRD content
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create project: ${response.statusText}`);
  }

  return await response.json();
}

// ============================================================================
// 2. Poll Project Status (wait for WBS generation)
// ============================================================================

interface ProjectStatus {
  project_id: string;
  status: string; // "CREATED" | "PLANNING" | "AWAITING_APPROVAL" | "IN_PROGRESS" | ...
  project_summary?: string;
  pending_tasks?: any[];
  in_progress_tasks?: any[];
  completed_tasks?: any[];
  error_message?: string;
}

async function getProjectStatus(projectId: string, authToken: string): Promise<ProjectStatus> {
  const response = await fetch(`${API_BASE}/projects/${projectId}/status`, {
    headers: {
      'Authorization': `Bearer ${authToken}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to get status: ${response.statusText}`);
  }

  return await response.json();
}

// Poll until status is AWAITING_APPROVAL
async function waitForPlan(projectId: string, authToken: string): Promise<ProjectStatus> {
  while (true) {
    const status = await getProjectStatus(projectId, authToken);

    console.log(`Project status: ${status.status}`);

    if (status.status === 'AWAITING_APPROVAL') {
      return status; // WBS is ready!
    }

    if (status.status === 'FAILED') {
      throw new Error(`Planning failed: ${status.error_message}`);
    }

    // Wait 5 seconds before polling again
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

// ============================================================================
// 3. Fetch Generated WBS Plan
// ============================================================================

interface Task {
  id: string;
  title: string;
  description: string;
  skill_required: string;
  acceptance_criteria: string[];
  depends_on: string[];
}

interface WBSPlan {
  project_id: string;
  project_summary: string;
  total_tasks: number;
  skill_breakdown: Record<string, number>;
  tasks: Task[];
  plan_approved: boolean;
  status: string;
}

async function getWBSPlan(projectId: string, authToken: string): Promise<WBSPlan> {
  const response = await fetch(`${API_BASE}/projects/${projectId}/plan`, {
    headers: {
      'Authorization': `Bearer ${authToken}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to get plan: ${response.statusText}`);
  }

  return await response.json();
}

// ============================================================================
// 4. Approve or Reject WBS Plan
// ============================================================================

interface ApprovalRequest {
  approved: boolean;
  feedback?: string; // Required if approved=false
}

interface ApprovalResponse {
  status: 'resumed' | 'replanning';
  message: string;
}

async function approvePlan(
  projectId: string,
  approved: boolean,
  authToken: string,
  feedback?: string
): Promise<ApprovalResponse> {
  const body: ApprovalRequest = { approved };
  if (!approved && feedback) {
    body.feedback = feedback;
  }

  const response = await fetch(`${API_BASE}/projects/${projectId}/plan/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Failed to approve plan: ${response.statusText}`);
  }

  return await response.json();
}

// ============================================================================
// 5. Complete Flow Example
// ============================================================================

async function completeBRDToCodeFlow(brdText: string, authToken: string) {
  console.log('📝 Step 1: Submitting BRD...');
  const project = await createProjectFromBRD(brdText, authToken);
  console.log(`✅ Project created: ${project.id}`);

  console.log('\n⏳ Step 2: Waiting for WBS generation (10-15 minutes)...');
  await waitForPlan(project.id, authToken);
  console.log('✅ WBS generation complete!');

  console.log('\n📋 Step 3: Fetching generated WBS...');
  const wbs = await getWBSPlan(project.id, authToken);
  console.log(`✅ WBS has ${wbs.total_tasks} tasks`);
  console.log('Task breakdown:', wbs.skill_breakdown);

  // Display WBS to user in your UI
  displayWBSToUser(wbs);

  // Wait for user decision
  const userDecision = await promptUserForApproval();

  if (userDecision.approved) {
    console.log('\n✅ Step 4: Approving plan and starting code generation...');
    await approvePlan(project.id, true, authToken);
    console.log('🚀 Code generation started!');
  } else {
    console.log('\n❌ Step 4: Rejecting plan with feedback...');
    await approvePlan(project.id, false, authToken, userDecision.feedback);
    console.log('🔄 Regenerating plan with feedback...');
    // Loop back to step 2
  }
}

// ============================================================================
// Helper Functions (implement based on your UI framework)
// ============================================================================

function displayWBSToUser(wbs: WBSPlan) {
  // Render WBS in your UI
  // Show: project_summary, total_tasks, skill_breakdown, task list
  console.log('Summary:', wbs.project_summary);
  console.log('Tasks:', wbs.tasks);
}

async function promptUserForApproval(): Promise<{ approved: boolean; feedback?: string }> {
  // Show approval dialog to user
  // Return their decision
  return { approved: true };
}

// ============================================================================
// React Component Example
// ============================================================================

import { useState } from 'react';

function BRDSubmissionForm() {
  const [brdText, setBrdText] = useState('');
  const [projectId, setProjectId] = useState<string | null>(null);
  const [status, setStatus] = useState('idle');
  const [wbs, setWbs] = useState<WBSPlan | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    try {
      setStatus('submitting');

      // Get auth token from your auth system
      const authToken = getAuthToken();

      // Create project
      const project = await createProjectFromBRD(brdText, authToken);
      setProjectId(project.id);

      setStatus('planning');

      // Wait for WBS
      await waitForPlan(project.id, authToken);

      // Fetch WBS
      const generatedWbs = await getWBSPlan(project.id, authToken);
      setWbs(generatedWbs);

      setStatus('awaiting_approval');

    } catch (error) {
      console.error('Error:', error);
      setStatus('error');
    }
  };

  const handleApprove = async () => {
    if (!projectId) return;

    try {
      const authToken = getAuthToken();
      await approvePlan(projectId, true, authToken);
      setStatus('approved');
    } catch (error) {
      console.error('Error:', error);
    }
  };

  const handleReject = async (feedback: string) => {
    if (!projectId) return;

    try {
      const authToken = getAuthToken();
      await approvePlan(projectId, false, authToken, feedback);
      setStatus('replanning');
      // Loop back to waiting for plan
      await waitForPlan(projectId, authToken);
      const newWbs = await getWBSPlan(projectId, authToken);
      setWbs(newWbs);
      setStatus('awaiting_approval');
    } catch (error) {
      console.error('Error:', error);
    }
  };

  return (
    <div>
      {status === 'idle' && (
        <form onSubmit={handleSubmit}>
          <h2>Submit Business Requirements Document</h2>
          <textarea
            value={brdText}
            onChange={(e) => setBrdText(e.target.value)}
            placeholder="Paste your BRD here (markdown format)..."
            rows={20}
            cols={80}
            minLength={10}
            maxLength={5000}
            required
          />
          <button type="submit">Generate WBS</button>
        </form>
      )}

      {status === 'submitting' && <div>Creating project...</div>}

      {status === 'planning' && (
        <div>
          <h3>Generating Work Breakdown Structure...</h3>
          <p>This takes 10-15 minutes. Processing 9 stages:</p>
          <ul>
            <li>Requirements extraction</li>
            <li>Scope definition</li>
            <li>Proposal generation</li>
            <li>Architecture design</li>
            <li>SOW generation</li>
            <li>WBS per module</li>
            <li>Test case generation</li>
            <li>Project analysis</li>
            <li>Task transformation</li>
          </ul>
          <div className="spinner">Loading...</div>
        </div>
      )}

      {status === 'awaiting_approval' && wbs && (
        <div>
          <h2>Work Breakdown Structure Generated</h2>

          <div className="summary">
            <h3>Summary</h3>
            <p>{wbs.project_summary}</p>
            <p><strong>Total Tasks:</strong> {wbs.total_tasks}</p>
          </div>

          <div className="skill-breakdown">
            <h3>Agent Distribution</h3>
            {Object.entries(wbs.skill_breakdown).map(([skill, count]) => (
              <div key={skill}>
                {skill}: {count} tasks
              </div>
            ))}
          </div>

          <div className="tasks">
            <h3>Tasks</h3>
            {wbs.tasks.map(task => (
              <div key={task.id} className="task-card">
                <h4>[{task.id}] {task.title}</h4>
                <p>{task.description}</p>
                <p><strong>Agent:</strong> {task.skill_required}</p>
                <p><strong>Dependencies:</strong> {task.depends_on.join(', ') || 'none'}</p>
              </div>
            ))}
          </div>

          <div className="actions">
            <button onClick={handleApprove} className="approve">
              ✅ Approve & Start Code Generation
            </button>
            <button onClick={() => {
              const feedback = prompt('Enter feedback for regeneration:');
              if (feedback) handleReject(feedback);
            }} className="reject">
              ❌ Reject & Regenerate
            </button>
          </div>
        </div>
      )}

      {status === 'approved' && (
        <div>
          <h3>✅ Plan Approved!</h3>
          <p>Code generation has started. Check project status for progress.</p>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Utility: Get auth token from your auth system
// ============================================================================

function getAuthToken(): string {
  // Get JWT token from localStorage, cookies, or your auth state
  return localStorage.getItem('authToken') || '';
}

export {
  createProjectFromBRD,
  getProjectStatus,
  waitForPlan,
  getWBSPlan,
  approvePlan,
  BRDSubmissionForm,
};
