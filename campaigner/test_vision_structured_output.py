import json,unittest
from creative_vision import FIELDS,vision_output_schema,vision_cli_args,parse_structured_vision_events,CreativeVision


class StructuredVisionTests(unittest.TestCase):
    def result(self,**extra):
        return {'type':'result','subtype':'success','is_error':False,**extra}

    def test_schema_exact_fields_and_strict_groups(self):
        schema=vision_output_schema()
        self.assertEqual(set(schema['required']),{'assets','proposed_adsets'})
        self.assertFalse(schema['additionalProperties'])
        asset=schema['properties']['assets']['items']
        self.assertEqual(set(asset['properties']),FIELDS)
        self.assertEqual(set(asset['required']),FIELDS)
        self.assertFalse(asset['additionalProperties'])
        self.assertFalse(schema['properties']['proposed_adsets']['items']['additionalProperties'])
        self.assertEqual(schema['properties']['assets']['maxItems'],30)

    def test_cli_flags_preserve_no_tools_and_bounded_turns(self):
        args=vision_cli_args('claude')
        self.assertEqual(args[args.index('--tools')+1],'')
        self.assertEqual(args[args.index('--max-turns')+1],'3')  # no tools; extra turns only let a schema retry finish
        self.assertIn('--safe-mode',args);self.assertIn('--no-chrome',args)
        self.assertEqual(json.loads(args[args.index('--mcp-config')+1]),{'mcpServers':{}})
        self.assertEqual(json.loads(args[args.index('--json-schema')+1]),vision_output_schema())

    def test_success_uses_structured_output_not_text(self):
        output={'assets':[],'proposed_adsets':[]}
        self.assertEqual(parse_structured_vision_events([self.result(structured_output=output,result='ignored free text')]),output)

    def test_missing_malformed_failed_or_duplicate_result_rejected(self):
        output={'assets':[],'proposed_adsets':[]}
        for events in ([self.result(result=json.dumps(output))],[self.result(structured_output=json.dumps(output))],
                       [self.result(structured_output=output,is_error=True)],
                       [self.result(structured_output=output,subtype='error_max_structured_output_retries')],
                       [self.result(structured_output=output)]*2,[None]):
            with self.subTest(events=events),self.assertRaises(ValueError):parse_structured_vision_events(events)

    def test_structured_shape_never_bypasses_source_validation(self):
        row={k:'test' for k in FIELDS};row.update(asset_id='source_asset_id',sha256='0'*64,format='feed',paired_asset_id=None,confidence=.8,warnings=[])
        output={'assets':[row],'proposed_adsets':[]}
        parsed=parse_structured_vision_events([self.result(structured_output=output)])
        with self.assertRaisesRegex(ValueError,'source binding'):
            CreativeVision._validate(parsed,[{'asset_id':'source_asset_id','sha256':'1'*64,'format':'feed'}])

    def test_nonzero_exit_preserves_safe_failure_code_not_private_error(self):
        event=self.result(subtype='error_max_turns',is_error=True,errors=['private token and customer details'])
        with self.assertRaisesRegex(ValueError,'error_max_turns') as raised:
            parse_structured_vision_events([event],returncode=1)
        self.assertNotIn('private',str(raised.exception));self.assertNotIn('customer',str(raised.exception))
        event['subtype']='untrusted private error code'
        with self.assertRaises(ValueError) as raised:parse_structured_vision_events([event],returncode=1)
        self.assertNotIn('untrusted',str(raised.exception))

    def test_nonzero_success_cannot_be_accepted(self):
        with self.assertRaises(ValueError):
            parse_structured_vision_events([self.result(structured_output={'assets':[],'proposed_adsets':[]})],returncode=1)

if __name__=='__main__':unittest.main()
