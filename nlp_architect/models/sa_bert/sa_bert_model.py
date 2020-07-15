import math
from torch import nn
import torch
from torch.nn import CrossEntropyLoss
from transformers.modeling_bert import BertEncoder, BertLayer, \
        BertAttention, BertSelfAttention, BertSelfOutput, BertConfig
from transformers import BertForTokenClassification, BertModel
# pylint: disable=no-member, not-callable, arguments-differ, missing-class-docstring

class SaBertConfig(BertConfig):
    def __init__(self, **kwargs):
        super().__init__()
        self.li_layer: int
        self.replace_final: list
        self.random_init: list
        self.all_layers: list
        self.duplicated_rels: list
        self.transpose: list
        self.layers_range: list

    def add_extra_args(self, hparams):
        self.li_layer = hparams.li_layer
        self.replace_final = hparams.replace_final
        self.random_init = hparams.random_init
        self.all_layers = hparams.all_layers
        self.duplicated_rels = hparams.duplicated_rels
        self.transpose = hparams.transpose
        self.layers_range = hparams.layers_range

class SaBertForToken(BertForTokenClassification):
    def __init__(self, config):
        super(SaBertForToken, self).__init__(config)
        self.bert = SaBertExtModel(config)

    def forward(
            self,
            input_ids=None,
            attention_mask=None,
            token_type_ids=None,
            position_ids=None,
            head_mask=None,
            inputs_embeds=None,
            labels=None,
            output_attentions=None,
            output_hidden_states=None,
            head_probs=None
        ):
        outputs = self.bert(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            head_probs=head_probs
        )

        sequence_output = outputs[0]

        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)

        outputs = (logits,) + outputs[2:]  # add hidden states and attention if they are here
        if labels is not None:
            loss_fct = CrossEntropyLoss()
            # Only keep active parts of the loss
            if attention_mask is not None:
                active_loss = attention_mask.view(-1) == 1
                active_logits = logits.view(-1, self.num_labels)
                active_labels = torch.where(
                    active_loss, labels.view(-1),
                    torch.tensor(loss_fct.ignore_index).type_as(labels)
                )
                loss = loss_fct(active_logits, active_labels)
            else:
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
            outputs = (loss,) + outputs

        return outputs  # (loss), scores, (hidden_states), (attentions)

class SaBertExtModel(BertModel):
    def __init__(self, config):
        super(SaBertExtModel, self).__init__(config)
        self.encoder = SaBertExtEncoder(config)

    def forward(
            self,
            input_ids=None,
            attention_mask=None,
            token_type_ids=None,
            position_ids=None,
            head_mask=None,
            inputs_embeds=None,
            encoder_hidden_states=None,
            encoder_attention_mask=None,
            output_attentions=None,
            output_hidden_states=None,
            head_probs=None
        ):
        r"""
    Return:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.BertConfig`) and inputs:
        last_hidden_state (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length, hidden_size)`):
            Sequence of hidden-states at the output of the last layer of the model.
        pooler_output (:obj:`torch.FloatTensor`: of shape :obj:`(batch_size, hidden_size)`):
            Last layer hidden-state of the first token of the sequence (classification token)
            further processed by a Linear layer and a Tanh activation function. The Linear
            layer weights are trained from the next sentence prediction (classification)
            objective during pre-training.

            This output is usually *not* a good summary
            of the semantic content of the input, you're often better with averaging or pooling
            the sequence of hidden-states for the whole input sequence.
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``output_hidden_states=True`` is passed or when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``output_attentions=True`` is passed or when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.
        """
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )

        if input_ids is not None and inputs_embeds is not None:
            raise ValueError("You cannot specify both input_ids and inputs_embeds at the same time")
        elif input_ids is not None:
            input_shape = input_ids.size()
        elif inputs_embeds is not None:
            input_shape = inputs_embeds.size()[:-1]
        else:
            raise ValueError("You have to specify either input_ids or inputs_embeds")

        device = input_ids.device if input_ids is not None else inputs_embeds.device

        if attention_mask is None:
            attention_mask = torch.ones(input_shape, device=device)
        if token_type_ids is None:
            token_type_ids = torch.zeros(input_shape, dtype=torch.long, device=device)

        # We can provide a self-attention mask of dimensions [batch_size, from_seq_length, to_seq_length]
        # ourselves in which case we just need to make it broadcastable to all heads.
        extended_attention_mask: torch.Tensor = self.get_extended_attention_mask(attention_mask, input_shape, device)

        # If a 2D ou 3D attention mask is provided for the cross-attention
        # we need to make broadcastabe to [batch_size, num_heads, seq_length, seq_length]
        if self.config.is_decoder and encoder_hidden_states is not None:
            encoder_batch_size, encoder_sequence_length, _ = encoder_hidden_states.size()
            encoder_hidden_shape = (encoder_batch_size, encoder_sequence_length)
            if encoder_attention_mask is None:
                encoder_attention_mask = torch.ones(encoder_hidden_shape, device=device)
            encoder_extended_attention_mask = self.invert_attention_mask(encoder_attention_mask)
        else:
            encoder_extended_attention_mask = None

        # Prepare head mask if needed
        # 1.0 in head_mask indicate we keep the head
        # attention_probs has shape bsz x n_heads x N x N
        # input head_mask has shape [num_heads] or [num_hidden_layers x num_heads]
        # and head_mask is converted to shape [num_hidden_layers x batch x num_heads x seq_length x seq_length]
        head_mask = self.get_head_mask(head_mask, self.config.num_hidden_layers)

        embedding_output = self.embeddings(
            input_ids=input_ids, position_ids=position_ids, token_type_ids=token_type_ids, inputs_embeds=inputs_embeds
        )
        encoder_outputs = self.encoder(
            embedding_output,
            attention_mask=extended_attention_mask,
            head_mask=head_mask,
            encoder_hidden_states=encoder_hidden_states,
            encoder_attention_mask=encoder_extended_attention_mask,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            head_probs=head_probs
        )
        sequence_output = encoder_outputs[0]
        pooled_output = self.pooler(sequence_output)

        outputs = (sequence_output, pooled_output,) + encoder_outputs[
            1:
        ]  # add hidden_states and attentions if they are here
        return outputs  # sequence_output, pooled_output, (hidden_states), (attentions)

class SaBertExtEncoder(BertEncoder):
    def __init__(self, config):
        super(SaBertExtEncoder, self).__init__(config)
        self.layer = nn.ModuleList([SaBertExtLayer(config, layer_num) for \
            layer_num in range(config.num_hidden_layers)])
        self.li_layer = config.li_layer
        self.all_layers = config.all_layers
        self.layers_range = config.layers_range

    def forward(
            self,
            hidden_states,
            attention_mask=None,
            head_mask=None,
            encoder_hidden_states=None,
            encoder_attention_mask=None,
            output_attentions=False,
            output_hidden_states=False,
            head_probs=None
        ):
        all_hidden_states = ()
        all_attentions = ()
        for i, layer_module in enumerate(self.layer):
            if output_hidden_states:
                all_hidden_states = all_hidden_states + (hidden_states,)

            head_probs_layer = None
            if self.all_layers or i == self.li_layer or i in self.layers_range:
                head_probs_layer = head_probs

            if getattr(self.config, "gradient_checkpointing", False):
                def create_custom_forward(module):
                    def custom_forward(*inputs):
                        return module(*inputs, output_attentions)
                    return custom_forward

                layer_outputs = torch.utils.checkpoint.checkpoint(
                    create_custom_forward(layer_module),
                    hidden_states,
                    attention_mask,
                    head_mask[i],
                    encoder_hidden_states,
                    encoder_attention_mask,
                    head_probs_layer
                )
            else:
                layer_outputs = layer_module(
                    hidden_states,
                    attention_mask,
                    head_mask[i],
                    encoder_hidden_states,
                    encoder_attention_mask,
                    head_probs_layer,
                    output_attentions
                )
            hidden_states = layer_outputs[0]

            if output_attentions:
                all_attentions = all_attentions + (layer_outputs[1],)

        # Add last layer
        if output_hidden_states:
            all_hidden_states = all_hidden_states + (hidden_states,)

        outputs = (hidden_states,)
        if output_hidden_states:
            outputs = outputs + (all_hidden_states,)
        if output_attentions:
            outputs = outputs + (all_attentions,)
        return outputs  # last-layer hidden state, (all hidden states), (all attentions)

class SaBertExtLayer(BertLayer):
    def __init__(self, config, layer_num):
        super(SaBertExtLayer, self).__init__(config)
        self.attention = SaBertExtAttention(config, layer_num)

    def forward(self, hidden_states, attention_mask=None, head_mask=None,
                encoder_hidden_states=None, encoder_attention_mask=None,
                head_probs=None, output_attentions=False):
        self_attention_outputs = self.attention(
            hidden_states, attention_mask, head_mask, output_attentions=output_attentions,
            head_probs=head_probs
        )
        attention_output = self_attention_outputs[0]
        outputs = self_attention_outputs[1:]  # add self attentions if we output attention weights

        if self.is_decoder and encoder_hidden_states is not None:
            cross_attention_outputs = self.crossattention(
                attention_output,
                attention_mask,
                head_mask,
                encoder_hidden_states,
                encoder_attention_mask,
                output_attentions,
                head_probs
            )
            attention_output = cross_attention_outputs[0]
            # add cross attentions if we output attention weights
            outputs = outputs + cross_attention_outputs[1:]

        intermediate_output = self.intermediate(attention_output)
        layer_output = self.output(intermediate_output, attention_output)
        outputs = (layer_output,) + outputs
        return outputs

class SaBertExtAttention(BertAttention):
    def __init__(self, config, layer_num):
        super(SaBertExtAttention, self).__init__(config)
        self.self = SaBertExtSelfAttention(config, layer_num)
        self.output = SaBertExtSelfOutput(config, layer_num)
        self.pruned_heads = set()

    def forward(self, hidden_states, attention_mask=None, head_mask=None, 
                encoder_hidden_states=None, encoder_attention_mask=None, 
                output_attentions=False, head_probs=None):
        self_outputs = self.self(hidden_states, attention_mask, head_mask, encoder_hidden_states,
                                 encoder_attention_mask, output_attentions, head_probs)
        attention_output = self.output(self_outputs[0], hidden_states)
        outputs = (attention_output,) + self_outputs[1:]  # add attentions if we output them
        return outputs

class SaBertExtSelfAttention(BertSelfAttention):
    def __init__(self, config, layer_num):
        super(SaBertExtSelfAttention, self).__init__(config)
        self.orig_num_attention_heads = config.num_attention_heads
        self.replace_final = config.replace_final
        self.random_init = config.random_init
        self.duplicated_rels = config.duplicated_rels
        self.transpose = config.transpose

        #self.attention_head_size = int(config.hidden_size / config.num_attention_heads)
        if  (layer_num == config.li_layer or config.all_layers is True \
             or layer_num in config.layers_range):
            self.num_attention_heads = 13
            self.extra_query = nn.Linear(config.hidden_size, self.attention_head_size)
            self.extra_key = nn.Linear(config.hidden_size, self.attention_head_size)
            self.extra_value = nn.Linear(config.hidden_size, self.attention_head_size)
            nn.init.normal_(self.extra_key.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_key.bias.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_query.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_query.bias.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_value.weight.data, mean=0, std=0.02)
            nn.init.normal_(self.extra_value.bias.data, mean=0, std=0.02)

    def forward(self, hidden_states, attention_mask=None, head_mask=None,
                encoder_hidden_states=None, encoder_attention_mask=None,
                output_attentions=False, head_probs=None):

        if head_probs is not None:
            mixed_query_layer = \
                torch.cat((self.query(hidden_states), self.extra_query(hidden_states)), 2)
        else:
            mixed_query_layer = self.query(hidden_states)

        # If this is instantiated as a cross-attention module, the keys
        # and values come from an encoder; the attention mask needs to be
        # such that the encoder's padding tokens are not attended to.
        if encoder_hidden_states is not None:
            mixed_key_layer = self.key(encoder_hidden_states)
            mixed_value_layer = self.value(encoder_hidden_states)
            attention_mask = encoder_attention_mask
        else:
            if head_probs is not None:
                self.all_head_size = self.num_attention_heads * self.attention_head_size
                mixed_key_layer = \
                    torch.cat((self.key(hidden_states), self.extra_key(hidden_states)), 2)
                mixed_value_layer = \
                    torch.cat((self.value(hidden_states), self.extra_value(hidden_states)), 2)
            else:
                self.all_head_size = self.num_attention_heads * self.attention_head_size
                mixed_key_layer = self.key(hidden_states)
                mixed_value_layer = self.value(hidden_states)

        query_layer = self.transpose_for_scores(mixed_query_layer)
        key_layer = self.transpose_for_scores(mixed_key_layer)
        value_layer = self.transpose_for_scores(mixed_value_layer)

        # Take the dot product between "query" and "key" to get the raw attention scores.
        attention_scores = torch.matmul(query_layer, key_layer.transpose(-1, -2))


        if head_probs is not None and not self.random_init:
            #  duplicated heads across all matrix (one vector duplicated across matrix)
            if self.duplicated_rels is True:
                head_probs = head_probs.sum(1, keepdim=True)
                # duplicate sum vector
                head_probs = head_probs.repeat(1, 64, 1)

            head_probs_norm = head_probs / head_probs.max(2, keepdim=True)[0]
            head_probs_norm[torch.isnan(head_probs_norm)] = 0
            
           # _, indices = head_probs_norm.max(2)
           # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
           # ex_head_attention_probs = torch.zeros(head_probs_norm.shape).to(device)
            
           # for batch, tokens in enumerate(indices):
           #     mask_matrix = torch.zeros([head_probs_norm.shape[1], head_probs_norm.shape[2]])
           #     i=0
           #     for token in tokens:
           #         if token != 0:
           #             mask_matrix[i][token] = 1.                        
           #             i=i+1                        

            #    ex_head_attention_probs[batch] = mask_matrix         

            #if self.duplicated_rels is True:
            #    head_probs_norm = ex_head_attention_probs

            original_12head_attn_scores = attention_scores[:, :self.orig_num_attention_heads]
            original_12head_attn_scores = original_12head_attn_scores / math.sqrt(self.attention_head_size)
            original_12head_attn_scores = original_12head_attn_scores + attention_mask
            original_12head_attn_probs = nn.Softmax(dim=-1)(original_12head_attn_scores)

            extra_head_attn = attention_scores[:,self.orig_num_attention_heads,:,:] 
            head_probs_norm = head_probs_norm*8+ attention_mask.squeeze(1)
            
                    
            if self.replace_final is False: 
                if self.transpose == True:                
                    head_probs_norm = head_probs_norm.transpose(-1, -2)                   

                extra_head_scaled_attn = ((extra_head_attn *8) * head_probs_norm).unsqueeze(1)       
                extra_head_scaled_attn = extra_head_scaled_attn + attention_mask
                extra_head_scaled_attn_probs = nn.Softmax(dim=-1)(extra_head_scaled_attn)
                attention_probs = torch.cat((original_12head_attn_probs, extra_head_scaled_attn_probs), 1)
           
            # if self.replace_final is True:
            #     attention_probs = torch.cat((original_12head_attn_probs, ex_head_attention_probs.unsqueeze(1)),1)

        if head_probs is None or self.random_init:            
            attention_scores = attention_scores / math.sqrt(self.attention_head_size)
            if attention_mask is not None:
                # Apply the attention mask is (precomputed for all layers in BertModel forward() function)
                attention_scores = attention_scores + attention_mask

            # Normalize the attention scores to probabilities.
            attention_probs = nn.Softmax(dim=-1)(attention_scores)

        # This is actually dropping out entire tokens to attend to, which might
        # seem a bit unusual, but is taken from the original Transformer paper.
        attention_probs = self.dropout(attention_probs)

        # Mask heads if we want to
        if head_mask is not None:
            attention_probs = attention_probs * head_mask

        context_layer = torch.matmul(attention_probs, value_layer)

        context_layer = context_layer.permute(0, 2, 1, 3).contiguous()
        new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
        context_layer = context_layer.view(*new_context_layer_shape)

        outputs = (context_layer, attention_probs) if output_attentions else (context_layer,)
        return outputs

    # def forward(self, hidden_states, attention_mask, head_mask=None, head_probs=None):    
    #     if head_probs is not None:
    #         self.all_head_size = self.num_attention_heads * self.attention_head_size            
    #         mixed_query_layer = torch.cat((self.query(hidden_states), self.extra_query(hidden_states)),2)
    #         mixed_key_layer = torch.cat((self.key(hidden_states), self.extra_key(hidden_states)),2)
    #         mixed_value_layer = torch.cat((self.value(hidden_states), self.extra_value(hidden_states)),2)
  
    #     else:
    #         self.all_head_size = self.num_attention_heads * self.attention_head_size
    #         mixed_query_layer = self.query(hidden_states)
    #         mixed_key_layer = self.key(hidden_states)
    #         mixed_value_layer = self.value(hidden_states)

    #     query_layer = self.transpose_for_scores(mixed_query_layer)
    #     key_layer = self.transpose_for_scores(mixed_key_layer)
    #     value_layer = self.transpose_for_scores(mixed_value_layer)

    #     attention_scores = torch.matmul(query_layer, key_layer.transpose(-1, -2))        

    #     if head_probs is not None and self.random_init is False:   

    #         #  duplicated heads across all matrix (one vector duplicated across matrix)
    #         if self.duplicated_rels is True:
    #             head_probs = head_probs.sum(1, keepdim=True)
    #             # duplicate sum vector
    #             head_probs = head_probs.repeat(1,64,1)
                 
    #         head_probs_norm = head_probs / head_probs.max(2, keepdim=True)[0]
    #         head_probs_norm[torch.isnan(head_probs_norm)] = 0
            
    #        # _, indices = head_probs_norm.max(2)
    #        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    #        # ex_head_attention_probs = torch.zeros(head_probs_norm.shape).to(device)
            
    #        # for batch, tokens in enumerate(indices):
    #        #     mask_matrix = torch.zeros([head_probs_norm.shape[1], head_probs_norm.shape[2]])
    #        #     i=0
    #        #     for token in tokens:
    #        #         if token != 0:
    #        #             mask_matrix[i][token] = 1.                        
    #        #             i=i+1                        

    #         #    ex_head_attention_probs[batch] = mask_matrix         

    #         #if self.duplicated_rels is True:
    #         #    head_probs_norm = ex_head_attention_probs

    #         original_12head_attn_scores = attention_scores[:, :self.orig_num_attention_heads]
    #         original_12head_attn_scores = original_12head_attn_scores / math.sqrt(self.attention_head_size)
    #         original_12head_attn_scores = original_12head_attn_scores + attention_mask
    #         original_12head_attn_probs = nn.Softmax(dim=-1)(original_12head_attn_scores)

    #         extra_head_attn = attention_scores[:,self.orig_num_attention_heads,:,:] 
    #         head_probs_norm = head_probs_norm*8+ attention_mask.squeeze(1)
            
                    
    #         if not self.replace_final: 
    #             if self.transpose == True:                
    #                 head_probs_norm = head_probs_norm.transpose(-1, -2)                   

    #             extra_head_scaled_attn = ((extra_head_attn *8) * head_probs_norm).unsqueeze(1)       
    #             extra_head_scaled_attn = extra_head_scaled_attn + attention_mask
    #             extra_head_scaled_attn_probs = nn.Softmax(dim=-1)(extra_head_scaled_attn)
    #             attention_probs = torch.cat((original_12head_attn_probs, extra_head_scaled_attn_probs), 1)
           
    #         # else:
    #         #     attention_probs = torch.cat((original_12head_attn_probs, ex_head_attention_probs.unsqueeze(1)),1)

    #     if head_probs is None or self.random_init is True:
    #         attention_scores = attention_scores / math.sqrt(self.attention_head_size)
    #         # Apply the attention mask is (precomputed for all layers in BertModel forward() function)
    #         attention_scores = attention_scores + attention_mask

    #         # Normalize the attention scores to probabilities.
    #         attention_probs = nn.Softmax(dim=-1)(attention_scores)
            
    #     # This is actually dropping out entire tokens to attend to, which might
    #     # seem a bit unusual, but is taken from the original Transformer paper.
    #     attention_probs = self.dropout(attention_probs)

    #     # Mask heads if we want to
    #     if head_mask is not None:
    #         attention_probs = attention_probs * head_mask

    #     context_layer = torch.matmul(attention_probs, value_layer)

    #     context_layer = context_layer.permute(0, 2, 1, 3).contiguous()
    #     new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
    #     context_layer = context_layer.view(*new_context_layer_shape)

    #     outputs = (context_layer, attention_probs) if self.output_attentions else (context_layer,)
    #     return outputs

class SaBertExtSelfOutput(BertSelfOutput):
    def __init__(self, config, layer_num):
        super(SaBertExtSelfOutput, self).__init__(config)
        if  (layer_num == config.li_layer or config.all_layers is True \
             or layer_num in config.layers_range): 
            self.original_num_attention_heads = config.num_attention_heads
            self.attention_head_size = int(config.hidden_size / self.original_num_attention_heads)
            self.dense_extra_head = nn.Linear(self.attention_head_size, config.hidden_size)

    def forward(self, hidden_states, input_tensor, head_probs=None):
        if head_probs is not None:
            original_hidden_vec_size = self.original_num_attention_heads*self.attention_head_size
            hidden_states = self.dense(hidden_states[:,:,:original_hidden_vec_size]) + \
                self.dense_extra_head(hidden_states[:,:,original_hidden_vec_size:])
        else:
            hidden_states = self.dense(hidden_states)
        
        hidden_states = self.dropout(hidden_states)
        hidden_states = self.LayerNorm(hidden_states + input_tensor)
        return hidden_states