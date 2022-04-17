def generate_images(text, tokenizer, dalle, vae, top_k, top_p, images_num, image_prompts=None, temperature=1.0, bs=8,
                    seed=None, use_cache=True):
    # TODO docstring
    if seed is not None:
        utils.seed_everything(seed)
   
    vocab_size = dalle.get_param('vocab_size')
    text_seq_length = dalle.get_param('text_seq_length')
    image_seq_length = dalle.get_param('image_seq_length')
    total_seq_length = dalle.get_param('total_seq_length')
    device = dalle.get_param('device')

    text = text.lower().strip()
    input_ids = tokenizer.encode_text(text, text_seq_length=text_seq_length)
    pil_images, ppl_scores = [], []
    for chunk in more_itertools.chunked(range(images_num), bs):
        chunk_bs = len(chunk)
        with torch.no_grad():
            attention_mask = torch.tril(torch.ones((chunk_bs, 1, total_seq_length, total_seq_length), device=device))
            out = input_ids.unsqueeze(0).repeat(chunk_bs, 1).to(device)
            has_cache = False
            if image_prompts is not None:
                prompts_idx, prompts = image_prompts.image_prompts_idx, image_prompts.image_prompts
                prompts = prompts.repeat(chunk_bs, 1)
            for idx in tqdm(range(out.shape[1], total_seq_length)):
                idx -= text_seq_length
                if image_prompts is not None and idx in prompts_idx:
                    out = torch.cat((out, prompts[:, idx].unsqueeze(1)), dim=-1)
                else:
                    logits, has_cache = dalle(out, attention_mask,
                                               use_cache=use_cache, return_loss=False)
                    logits = logits[:, -1, vocab_size:]
                    logits /= temperature
                    filtered_logits = transformers.top_k_top_p_filtering(logits, top_k=top_k, top_p=top_p)
                    probs = torch.nn.functional.softmax(filtered_logits, dim=-1)
                    sample = torch.multinomial(probs, 1)
                    out = torch.cat((out, sample), dim=-1)

            codebooks = out[:, -image_seq_length:]
            logits, _ = dalle(out, attention_mask,  use_cache=use_cache, return_loss=False)
            logits = rearrange(logits, 'b n c -> b c n')
            image_logits = logits[:, vocab_size:, -image_seq_length:- 1].contiguous().float()
            out = out.contiguous().long()
            ppl_scores.append(
                ce_to_ppl(F.cross_entropy(
                    image_logits,
                    out[:, -image_seq_length + 1:],
                    reduction='none',
                ))
            )

            images = vae.decode(codebooks)
            pil_images += utils.torch_tensors_to_pil_list(images)

    ppl_scores = torch.cat(ppl_scores)
    indexes = ppl_scores.argsort()

    sorted_pil_images = []
    for idx in indexes:
        sorted_pil_images.append(pil_images[idx.item()])

    return sorted_pil_images, ppl_scores[indexes].cpu().numpy().tolist()
