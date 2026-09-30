const schema = {"type": "object", "additionalProperties": false, "required": ["reply", "actions"], "properties": {"reply": {"type": "string"}, "actions": {"type": "array", "items": {"type": "object", "additionalProperties": false, "required": ["type", "target_id", "source_id", "role", "field", "value", "evidence"], "properties": {"type": {"type": "string", "enum": ["add_participant", "save_field", "reuse_field", "focus_participant"]}, "target_id": {"type": "string"}, "source_id": {"type": ["string", "null"]}, "role": {"type": ["string", "null"], "enum": ["aval", "representante", null]}, "field": {"type": ["string", "null"]}, "value": {"type": ["string", "null"]}, "evidence": {"type": "string"}}}}}};

const incoming = $input.first().json;
const body = incoming.body;
if (!body || body.version !== 1 || typeof body.message !== 'string' ||
    !body.message.trim() || body.message.length > 2000 || body.context?.version !== 1 ||
    !Array.isArray(body.context?.participants) || body.context.participants.length > 12 ||
    !Array.isArray(body.history) || body.history.length > 4 ||
    typeof incoming.instructions !== 'string' || !incoming.instructions.trim()) {
  return [{json:{ok:false,version:1,error:'invalid_request'}}];
}
return [{json:{ok:true,request:{
  model: incoming.model || 'gpt-5.4-mini',
  store:false, max_output_tokens:3000,
  instructions:incoming.instructions,
  input:[{role:'user',content:[{type:'input_text',text:JSON.stringify({message:body.message,context:body.context,history:body.history})}]}],
  text:{format:{type:'json_schema',name:'analease_capture',strict:true,schema}}
}}}];

