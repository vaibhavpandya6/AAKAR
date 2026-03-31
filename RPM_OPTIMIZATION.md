# BRD-to-WBS Pipeline - 39 RPM Optimization

## 🎯 Optimization Applied

The pipeline has been optimized to stay within **39 RPM (Requests Per Minute)** rate limit while using **nvidia/nemotron-3-super-120b-a12b** for all stages.

---

## ⚙️ Changes Made

### 1. **Single Model Strategy**
- **Before**: Different models for different stages (Nemotron, Qwen, DeepSeek, Minimax)
- **After**: Nemotron-3-Super-120B for ALL stages (Requirements, Scope, Proposal, Architecture, SOW, WBS, Tests)

**Benefits**:
- Consistent quality across all stages
- Simpler configuration
- Single model to optimize for

### 2. **Removed DeepSeek from Fallbacks**
- **Before**: `FALLBACK_MODELS = [qwen, deepseek]`
- **After**: `FALLBACK_MODELS = [qwen]`

**Benefits**:
- Reduces maximum API calls from 3 to 2 per stage
- DeepSeek was timing out (504 errors) anyway
- Qwen is sufficient as fallback

### 3. **Rate Limiting Implementation**
Added automatic throttling to ensure 39 RPM compliance:

```python
RPM_LIMIT = 39
MIN_INTERVAL = 60.0 / 39  # ~1.54 seconds between calls

async def _throttle_api_call():
    """Ensure we don't exceed 39 RPM by adding delays between API calls."""
    # Adds ~1.5s delay between consecutive API calls
```

**How it works**:
- Tracks timestamp of last API call
- Ensures minimum 1.54 seconds between calls
- Automatically adds sleep if calls are too frequent
- Uses asyncio.Lock to prevent race conditions

### 4. **Updated Configuration**

**`.env.example`**:
```bash
# Using Nemotron-3-Super-120B for all stages (optimized for 39 RPM)
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
```

**`config/settings.py`**:
```python
nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
nvidia_sow_model: str = "nvidia/nemotron-3-super-120b-a12b"  # Same as primary
nvidia_test_model: str = "nvidia/nemotron-3-super-120b-a12b"  # Same as primary
```

---

## 📊 API Call Breakdown

### Pipeline Stages

| Stage | API Calls | Time @ 39 RPM |
|-------|-----------|---------------|
| 1. Requirements | 1 | ~0s |
| 2. Scope | 1 | ~1.5s |
| 3. Proposal | 1 | ~1.5s |
| 4. Architecture | 1 | ~1.5s |
| 5. SOW | 1 | ~1.5s |
| 6. WBS (4 modules) | 4 | ~6s |
| 7. Tests (4 agents) | 4 | ~6s |
| 8. Analysis | 1 | ~1.5s |
| **Total** | **14** | **~21s overhead** |

### With Fallbacks (if primary fails)
- Maximum calls per stage: 2 (primary + qwen)
- Maximum total calls: 28 (if all stages need fallback)
- Maximum time overhead: ~42s

### Actual Pipeline Time
- **API call overhead**: ~21-42 seconds (throttling delays)
- **LLM processing time**: 10-15 minutes (actual generation)
- **Total**: ~11-16 minutes

**Note**: The throttling only adds ~30-60 seconds to the total pipeline time, which is negligible compared to the LLM processing time.

---

## 🔍 Rate Limit Compliance

### Without Throttling (Before)
- Pipeline could burst 14 API calls in <5 seconds
- If any stage retried → exceeded 39 RPM → rate limit errors

### With Throttling (After)
- Maximum rate: 39 calls/minute (guaranteed)
- Smooth distribution: ~1.5s between calls
- No rate limit errors even with retries

### Calculation
```
39 RPM = 39 calls / 60 seconds
Minimum interval = 60s / 39 = 1.538 seconds
```

---

## 🧪 Testing

Run the optimized pipeline:

```bash
python test_direct_brd.py
```

**Expected Output**:
```
✓ NVIDIA API key configured
✓ Model: nvidia/nemotron-3-super-120b-a12b (all stages)
✓ Fallback: qwen/qwen3.5-122b-a10b
✓ Rate Limit: 39 RPM (~1.5s between calls)
```

**Logs to Watch**:
```
[debug] rpm_throttle sleep_seconds=1.5
[info] nvidia_llm_call model=nvidia/nemotron-3-super-120b-a12b
```

---

## 📈 Performance Comparison

### Before Optimization
- **Models**: 4 different models (Nemotron, Qwen, DeepSeek, Minimax)
- **Fallbacks**: 3 attempts per stage
- **Rate Limit**: Uncontrolled bursts
- **Failures**: DeepSeek 504 timeouts, Minimax 410 deprecated
- **Total Time**: 10-15 min + frequent failures

### After Optimization
- **Models**: 1 model (Nemotron for everything)
- **Fallbacks**: 2 attempts per stage (Nemotron + Qwen)
- **Rate Limit**: Guaranteed 39 RPM compliance
- **Failures**: Reduced (only 2 models to fail)
- **Total Time**: 11-16 min (consistent, reliable)

---

## 🛡️ Error Handling

The pipeline gracefully handles failures:

1. **Primary model fails** → Falls back to Qwen automatically
2. **Both models fail** → Stage uses default/fallback values
3. **Scope fails** → Auto-generates scope from requirements
4. **Rate limit hit** → Automatic throttling prevents this

**Result**: Pipeline completes successfully even with partial failures.

---

## 🔧 Configuration Options

### Use Only Nemotron (Recommended)
```bash
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
```

### Override Specific Stages (Advanced)
```bash
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
NVIDIA_SOW_MODEL=qwen/qwen3.5-122b-a10b  # Use Qwen for SOW only
NVIDIA_TEST_MODEL=nvidia/nemotron-3-super-120b-a12b
```

### Adjust Rate Limit (Advanced)
Edit `orchestrator/nodes/brd_to_wbs_node.py`:
```python
RPM_LIMIT = 50  # Increase if your API plan allows
```

---

## ✅ Summary

**Optimizations**:
1. ✅ Single model (Nemotron) for all stages
2. ✅ Removed DeepSeek from fallbacks
3. ✅ Added 39 RPM rate limiting
4. ✅ Simplified configuration
5. ✅ Better error handling with scope fallback

**Results**:
- 🎯 100% rate limit compliance
- ⚡ ~30-60s overhead (minimal)
- 🛡️ Fault-tolerant with fallbacks
- 📊 Consistent quality across stages
- 🔧 Simpler to configure and maintain

**Trade-offs**:
- Slightly longer total time (~1 minute added for throttling)
- Less model diversity (but more consistent results)

The optimization ensures reliable, rate-limit-compliant operation while maintaining high-quality WBS generation! 🚀
