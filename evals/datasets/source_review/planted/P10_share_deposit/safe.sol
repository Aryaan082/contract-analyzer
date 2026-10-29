// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ShareLedger {
    uint256 public totalAssets;
    uint256 public totalShares;
    mapping(address => uint256) public sharesOf;

    function deposit(uint256 assets) external returns (uint256 shares) {
        require(assets > 0, "zero");
        shares = totalShares == 0
            ? assets
            : assets * totalShares / totalAssets;
        totalAssets += assets;
        totalShares += shares;
        sharesOf[msg.sender] += shares;
    }

    function redeem(uint256 shares) external returns (uint256 assets) {
        sharesOf[msg.sender] -= shares;
        assets = shares * totalAssets / totalShares;
        totalShares -= shares;
        totalAssets -= assets;
    }
}
