// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract DividendBook {
    address[] public holders;
    mapping(address => uint256) public stake;
    uint256 public totalStake;
    uint256 public cursor;
    mapping(address => uint256) public owed;

    function join() external payable {
        require(msg.value > 0, "empty");
        if (stake[msg.sender] == 0) holders.push(msg.sender);
        stake[msg.sender] += msg.value;
        totalStake += msg.value;
    }

    function distribute(uint256 count) external {
        uint256 pot = address(this).balance - totalStake;
        uint256 end = cursor + count > holders.length ? holders.length : cursor + count;
        for (uint256 i = cursor; i < end; i++) {
            address holder = holders[i];
            owed[holder] += pot * stake[holder] / totalStake;
        }
        cursor = end == holders.length ? 0 : end;
    }

    function collect() external {
        uint256 amount = owed[msg.sender];
        owed[msg.sender] = 0;
        payable(msg.sender).transfer(amount);
    }
}
