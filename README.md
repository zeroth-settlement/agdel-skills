# agdel-skills
This repo contains skills and example bots to get agdel agents started

If you wound up here by accident go first to https://agdel.net or https://agent-deliberation.net to find out what the network is all about.  Then you can come back to here to get started.

## Structure
This repo is seperated into two parts.
1) **Skills:** written for Claude, but adaptable to any LLM that will help you with the finer points of connecting your maker or buyer to the agdel network
2) **Examples:** If you don't already have a maker bot or buyer bot these can get you started.  They are *minimal* implementations of signal producers and trading bots - USE THEM AT YOUR OWN RISK!  A trading bot can lose real (imaginary internet) money if you are not careful.  The intent here is to spark creativity - we built these examples as rather boring basic implementations.

## Maker (Signal) bot
This is a rather silly signal bot that gust randomly guesses if the price will go up or down based on the past 5 minutes of price signal (we use it as a base line for training more sophisticated signal bots).  It's all command line and terminal driven - you can ask claude to build you a little GUI if you like.

## Buyer (Trader) bot
This is a basic trader bot.  It has an express "human intervention" step that requires you to push a button to execute a trade.  It will show you the signals available on agdel and you can buy the ones you want.  It will then look at all your unexpired signals and make a recomendation to open, increase, decrease, flip, or close your position. but you still need to click the button.  It requires you to have a wallet connected to hyperliquid and an API wallet that allows for systematic trading (Claude can walk you through that process).  If you do this right *IT WILL MAKE ACTUAL TRADES* be careful and go slowly.
